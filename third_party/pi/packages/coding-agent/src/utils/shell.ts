import { existsSync } from "node:fs";
import { delimiter } from "node:path";
import { spawn, spawnSync } from "child_process";
import { getBinDir } from "../config.ts";

export interface ShellConfig {
	shell: string;
	args: string[];
	commandTransport?: "argv" | "stdin";
}

/**
 * Find bash executable on PATH (cross-platform)
 */
function isLegacyWslBashPath(path: string): boolean {
	const normalized = path.replace(/\//g, "\\").toLowerCase();
	return /^[a-z]:\\windows\\(?:system32|sysnative)\\bash\.exe$/.test(normalized);
}

function getBashShellConfig(shell: string): ShellConfig {
	return isLegacyWslBashPath(shell) ? { shell, args: ["-s"], commandTransport: "stdin" } : { shell, args: ["-c"] };
}

function findBashOnPath(): string | null {
	if (process.platform === "win32") {
		// Windows: Use 'where' and verify file exists (where can return non-existent paths)
		try {
			const result = spawnSync("where", ["bash.exe"], {
				encoding: "utf-8",
				timeout: 5000,
				windowsHide: true,
			});
			if (result.status === 0 && result.stdout) {
				const firstMatch = result.stdout.trim().split(/\r?\n/)[0];
				if (firstMatch && existsSync(firstMatch)) {
					return firstMatch;
				}
			}
		} catch {
			// Ignore errors
		}
		return null;
	}

	// Unix: Use 'which' and trust its output (handles Termux and special filesystems)
	try {
		const result = spawnSync("which", ["bash"], { encoding: "utf-8", timeout: 5000 });
		if (result.status === 0 && result.stdout) {
			const firstMatch = result.stdout.trim().split(/\r?\n/)[0];
			if (firstMatch) {
				return firstMatch;
			}
		}
	} catch {
		// Ignore errors
	}
	return null;
}

/**
 * Resolve shell configuration based on platform and an optional explicit shell path.
 * Resolution order:
 * 1. User-specified shellPath
 * 2. On Windows: Git Bash in known locations, then bash on PATH
 * 3. On Unix: /bin/bash, then bash on PATH, then fallback to sh
 */
export function getShellConfig(customShellPath?: string): ShellConfig {
	// 1. Check user-specified shell path
	if (customShellPath) {
		if (existsSync(customShellPath)) {
			return getBashShellConfig(customShellPath);
		}
		throw new Error(`Custom shell path not found: ${customShellPath}`);
	}

	if (process.platform === "win32") {
		// 2. Try Git Bash in known locations
		const paths: string[] = [];
		const programFiles = process.env.ProgramFiles;
		if (programFiles) {
			paths.push(`${programFiles}\\Git\\bin\\bash.exe`);
		}
		const programFilesX86 = process.env["ProgramFiles(x86)"];
		if (programFilesX86) {
			paths.push(`${programFilesX86}\\Git\\bin\\bash.exe`);
		}

		for (const path of paths) {
			if (existsSync(path)) {
				return getBashShellConfig(path);
			}
		}

		// 3. Fallback: search bash.exe on PATH (Cygwin, MSYS2, WSL, etc.)
		const bashOnPath = findBashOnPath();
		if (bashOnPath) {
			return getBashShellConfig(bashOnPath);
		}

		throw new Error(
			`No bash shell found. Options:\n` +
				`  1. Install Git for Windows: https://git-scm.com/download/win\n` +
				`  2. Add your bash to PATH (Cygwin, MSYS2, etc.)\n` +
				"  3. Set shellPath in settings.json\n\n" +
				`Searched Git Bash in:\n${paths.map((p) => `  ${p}`).join("\n")}`,
		);
	}

	// Unix: try /bin/bash, then bash on PATH, then fallback to sh
	if (existsSync("/bin/bash")) {
		return getBashShellConfig("/bin/bash");
	}

	const bashOnPath = findBashOnPath();
	if (bashOnPath) {
		return getBashShellConfig(bashOnPath);
	}

	return { shell: "sh", args: ["-c"] };
}

export function getShellEnv(): NodeJS.ProcessEnv {
	const binDir = getBinDir();
	const pathKey = Object.keys(process.env).find((key) => key.toLowerCase() === "path") ?? "PATH";
	const currentPath = process.env[pathKey] ?? "";
	const pathEntries = currentPath.split(delimiter).filter(Boolean);
	const hasBinDir = pathEntries.includes(binDir);
	const updatedPath = hasBinDir ? currentPath : [binDir, currentPath].filter(Boolean).join(delimiter);

	return {
		...process.env,
		[pathKey]: updatedPath,
	};
}

/**
 * Cross-platform shell stdout/stderr decoding (Room agent tools).
 *
 * | Platform | Contract |
 * |----------|----------|
 * | macOS / Linux | UTF-8 only (native for bash/zsh/curl) |
 * | Windows 10/11 | Prefer UTF-8; if mojibake, pick best among
 *   common system ACPs (zh-CN CP936/GBK, Western 1252, JA/KR/TW, …).
 *   Covers Win10 legacy ACP, Win11 optional UTF-8 beta, PS 5.1 + pwsh. |
 */
function decodeScore(text: string): number {
	const bad = (text.match(/\uFFFD/g) || []).length;
	// Fewer replacements = better; slight penalty for C1 controls (bad decode)
	const ctrl = (text.match(/[\u0080-\u009F]/g) || []).length;
	return -(bad * 1000 + ctrl);
}

export function decodeShellBytes(data: Buffer | Uint8Array): string {
	const buf = Buffer.isBuffer(data) ? data : Buffer.from(data);
	if (buf.length === 0) {
		return "";
	}
	const utf8 = buf.toString("utf8");
	// macOS / Linux: tools and shells speak UTF-8
	if (process.platform !== "win32") {
		return utf8;
	}
	// Clean UTF-8 (curl, Node, Win11 UTF-8 system locale, pwsh often)
	if (!(utf8.includes("\uFFFD"))) {
		return utf8;
	}
	// Windows 10/11 mixed code pages — choose least-broken decode
	const encodings = [
		"utf-8",
		"gb18030",
		"gbk",
		"windows-1252",
		"shift_jis",
		"big5",
		"euc-kr",
		"windows-1251",
	] as const;
	let best = utf8;
	let bestScore = decodeScore(utf8);
	for (const enc of encodings) {
		if (enc === "utf-8") continue;
		try {
			const s = new TextDecoder(enc).decode(buf);
			const sc = decodeScore(s);
			if (sc > bestScore) {
				best = s;
				bestScore = sc;
			}
		} catch {
			// encoding may be unavailable in minimal ICU builds
		}
	}
	return best;
}

const PS_UTF8_PREAMBLE =
	"[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); " +
	"$OutputEncoding = [Console]::OutputEncoding; ";

/**
 * Force Windows PowerShell / pwsh -Command bodies to emit UTF-8 on the pipe.
 * No-op on macOS/Linux and for non-PowerShell commands.
 *
 * Win10: Windows PowerShell 5.1 defaults to system ACP (often CP936).
 * Win11: may be UTF-8 system-wide or still ACP; rewrite is safe either way.
 */
export function rewriteCommandForWindowsUtf8(command: string): string {
	if (process.platform !== "win32") {
		return command;
	}
	// powershell.exe (5.1) and pwsh.exe (Core 7+)
	if (!/\b(?:powershell|pwsh)(?:\.exe)?\b/i.test(command)) {
		return command;
	}
	if (/OutputEncoding/i.test(command) || /UTF8Encoding/i.test(command)) {
		return command;
	}
	const re =
		/(\b(?:powershell|pwsh)(?:\.exe)?\b)((?:\s+-[A-Za-z][A-Za-z0-9_]*(?:\s+"[^"]*"|\s+'[^']*'|\s+[^\s-]+)*)*?)\s+(-Command|-c)\s+(")([\s\S]*)(")\s*$/i;
	const m = command.match(re);
	if (!m) {
		const re2 =
			/(\b(?:powershell|pwsh)(?:\.exe)?\b)((?:\s+-[A-Za-z][A-Za-z0-9_]*(?:\s+"[^"]*"|\s+'[^']*'|\s+[^\s-]+)*)*?)\s+(-Command|-c)\s+(')([\s\S]*)(')\s*$/i;
		const m2 = command.match(re2);
		if (!m2) {
			return command;
		}
		const body = m2[5];
		const flags = m2[2] || "";
		const hasNoProfile = /(?:^|\s)-NoProfile(?:\s|$)/i.test(flags);
		const np = hasNoProfile ? "" : " -NoProfile";
		return `${m2[1]}${flags}${np} ${m2[3]} '${PS_UTF8_PREAMBLE}${body}'`;
	}
	const body = m[5];
	const flags = m[2] || "";
	const hasNoProfile = /(?:^|\s)-NoProfile(?:\s|$)/i.test(flags);
	const np = hasNoProfile ? "" : " -NoProfile";
	const escaped = PS_UTF8_PREAMBLE + body.replace(/"/g, '`"');
	return `${m[1]}${flags}${np} ${m[3]} "${escaped}"`;
}

/**
 * Sanitize binary output for display/storage.
 * Removes characters that crash string-width or cause display issues:
 * - Control characters (except tab, newline, carriage return)
 * - Lone surrogates
 * - Unicode Format characters (crash string-width due to a bug)
 * - Characters with undefined code points
 */
export function sanitizeBinaryOutput(str: string): string {
	// Use Array.from to properly iterate over code points (not code units)
	// This handles surrogate pairs correctly and catches edge cases where
	// codePointAt() might return undefined
	return Array.from(str)
		.filter((char) => {
			// Filter out characters that cause string-width to crash
			// This includes:
			// - Unicode format characters
			// - Lone surrogates (already filtered by Array.from)
			// - Control chars except \t \n \r
			// - Characters with undefined code points

			const code = char.codePointAt(0);

			// Skip if code point is undefined (edge case with invalid strings)
			if (code === undefined) return false;

			// Allow tab, newline, carriage return
			if (code === 0x09 || code === 0x0a || code === 0x0d) return true;

			// Filter out control characters (0x00-0x1F, except 0x09, 0x0a, 0x0x0d)
			if (code <= 0x1f) return false;

			// Filter out Unicode format characters
			if (code >= 0xfff9 && code <= 0xfffb) return false;

			return true;
		})
		.join("");
}

/**
 * Detached child processes must be tracked so they can be killed on parent
 * shutdown signals (SIGHUP/SIGTERM).
 */
const trackedDetachedChildPids = new Set<number>();

export function trackDetachedChildPid(pid: number): void {
	trackedDetachedChildPids.add(pid);
}

export function untrackDetachedChildPid(pid: number): void {
	trackedDetachedChildPids.delete(pid);
}

export function killTrackedDetachedChildren(): void {
	for (const pid of trackedDetachedChildPids) {
		killProcessTree(pid);
	}
	trackedDetachedChildPids.clear();
}

/**
 * Kill a process and all its children (cross-platform)
 */
export function killProcessTree(pid: number): void {
	if (process.platform === "win32") {
		// Use taskkill on Windows to kill process tree
		try {
			spawn("taskkill", ["/F", "/T", "/PID", String(pid)], {
				stdio: "ignore",
				detached: true,
				windowsHide: true,
			});
		} catch {
			// Ignore errors if taskkill fails
		}
	} else {
		// Use SIGKILL on Unix/Linux/Mac
		try {
			process.kill(-pid, "SIGKILL");
		} catch {
			// Fallback to killing just the child if process group kill fails
			try {
				process.kill(pid, "SIGKILL");
			} catch {
				// Process already dead
			}
		}
	}
}
