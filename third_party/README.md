# third_party

## pi (git submodule)

Room embeds a **Room-branded** build of [earendil-works/pi](https://github.com/earendil-works/pi).

| Path | Role |
|------|------|
| `third_party/pi` | **Only** Pi source used by Room packaging |
| `code.research/pi` | **Forbidden** for Room (other products) |
| `c-checkers/pi-room` | Deprecated sibling clone — prefer this submodule |

### First clone

```bash
git clone --recurse-submodules <room-tui-url>
# or after plain clone:
git submodule update --init --recursive
```

### Build Room pi binary

```bash
./scripts/build-room-pi.sh          # macOS / Linux
.\scripts\build-room-pi.ps1         # Windows
```

Build applies scheme B branding (`scripts/apply-room-pi-brand.py`):

```json
"piConfig": { "name": "room", "configDir": ".config/room-tui" }
```

Default config: `~/.config/room-tui/agent` via `ROOM_CODING_AGENT_DIR`.

### Upgrade upstream pi

```bash
cd third_party/pi
git fetch origin
git checkout <tag-or-commit>
cd ../..
git add third_party/pi
git commit -m "chore: bump third_party/pi"
```

After upgrade, re-run `build-room-pi` (brand is re-applied each build).

### Note on dirty submodule

`apply-room-pi-brand.py` may leave `packages/coding-agent/package.json` modified
inside the submodule. That is expected for local builds; Room does not require
pushing that change upstream. Reset if needed:

```bash
git -C third_party/pi checkout -- packages/coding-agent/package.json
```
