# zsh + Starship (Powerlevel10k Style) Setup on OpenSUSE Leap 15.6 WSL

**Target environment:** OpenSUSE Leap 15.6 · Windows Subsystem for Linux  
**Constraints:** Corporate proxy (no GitHub access) · All packages installed via `zypper`  
**Result:** zsh with Starship prompt (pastel-powerline preset), syntax highlighting, and autosuggestions

---

## Prerequisites

- Proxy is already configured in the WSL environment (`http_proxy` / `https_proxy`)
- A Nerd Font (e.g. MesloLGS NF, FiraCode NF, CaskaydiaCove NF) is installed on the Windows host and set as the font in Windows Terminal settings — this is **required** for powerline glyphs to render correctly
- You have `sudo` access inside WSL

If you need to verify proxy is reachable, run:
```zsh
curl -I https://download.opensuse.org
```
You should get an HTTP response. If not, check `/etc/sysconfig/proxy` or export proxy variables before proceeding:
```zsh
export http_proxy="http://<host>:<port>"
export https_proxy="http://<host>:<port>"
export no_proxy="localhost,127.0.0.1"
```

---

## Step 1 — Install zsh

zsh is available in the default Leap 15.6 `oss` repository:

```zsh
sudo zypper refresh
sudo zypper install zsh
```

Confirm the installation:
```zsh
zsh --version
# Expected: zsh 5.9 (or similar)
```

---

## Step 2 — Add OBS Repositories for zsh Plugins

The `zsh-syntax-highlighting` and `zsh-autosuggestions` packages are maintained in separate
OBS sub-projects under `shells:/zsh-users/`. Their Leap 15.6 builds are published against the
**15.4** target, but both packages are architecture-independent (`noarch`) and install cleanly on
Leap 15.6.

```zsh
# zsh-syntax-highlighting repo
sudo zypper addrepo --refresh \
  "https://download.opensuse.org/repositories/shells:/zsh-users:/zsh-syntax-highlighting/15.4/" \
  zsh-syntax-highlighting

# zsh-autosuggestions repo
sudo zypper addrepo --refresh \
  "https://download.opensuse.org/repositories/shells:/zsh-users:/zsh-autosuggestions/15.4/" \
  zsh-autosuggestions
```

Accept the GPG key when prompted (once per repo). Then refresh:
```zsh
sudo zypper refresh
```

---

## Step 3 — Install zsh Plugins

```zsh
sudo zypper install zsh-syntax-highlighting zsh-autosuggestions
```

Once installed, the plugin scripts are at:

| Plugin | Script path |
|--------|-------------|
| zsh-syntax-highlighting | `/usr/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh` |
| zsh-autosuggestions | `/usr/share/zsh-autosuggestions/zsh-autosuggestions.zsh` |

---

## Step 4 — Install Starship Prompt

Starship is not in the default Leap 15.6 `oss` repository. Try the official OBS `shells` project
first. If that does not provide a built package, fall back to the community home repository below.

### Option A — Official OBS `shells` project (try first)

```zsh
sudo zypper addrepo --refresh \
  "https://download.opensuse.org/repositories/shells/15.6/" \
  shells-leap-156

sudo zypper refresh
sudo zypper install starship
```

Verify it worked:
```zsh
starship --version
```

If `zypper` reports the package is not found or the repository has no `starship` package (which
can happen when the build fails in OBS due to Rust toolchain constraints), proceed to Option B.

### Option B — Community home repository fallback

The `home:Dead_Mozay` OBS home repository has historically maintained a working Leap 15.6
build of starship compiled against a statically linked Rust toolchain:

```zsh
sudo zypper addrepo --refresh \
  "https://download.opensuse.org/repositories/home:/Dead_Mozay/openSUSE_Leap_15.6/" \
  home-dead-mozay

sudo zypper refresh
sudo zypper install starship
```

> **Note:** If the community maintainer's build is also unavailable at the time you run this,
> search for an alternative at `https://software.opensuse.org/package/starship` from a browser,
> locate a **Leap 15.6** community build, click "Show experimental packages", copy the
> `zypper addrepo` command shown, and run it in WSL. The `download.opensuse.org` download URL
> will work through the corporate proxy.

### Option C — Build from source via Rust/Cargo (offline crate mirror required)

This path requires either external internet access to `crates.io` or an internal Cargo mirror
configured in `~/.cargo/config.toml`. If neither is available, skip this option.

```zsh
sudo zypper install rust cargo
cargo install starship --locked
```

The binary will be placed at `~/.cargo/bin/starship`. Ensure `~/.cargo/bin` is in your `PATH`.

---

## Step 5 — Set zsh as the Default Shell

```zsh
chsh -s /bin/zsh "$(whoami)"
```

You will be prompted for your password. After this, **close and reopen** the WSL terminal. All
new sessions will start in zsh.

To confirm:
```zsh
echo $SHELL
# Expected: /bin/zsh
```

---

## Step 6 — Configure `~/.zshrc`

Back up any existing configuration before overwriting:
```zsh
[ -f ~/.zshrc ] && cp ~/.zshrc ~/.zshrc.bak
```

Then create or replace `~/.zshrc` with the following content:

```zsh
# ~/.zshrc — zsh configuration with Starship, syntax highlighting, and autosuggestions

# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
HISTFILE=~/.zsh_history
HISTSIZE=10000
SAVEHIST=10000
setopt HIST_IGNORE_DUPS       # Do not record duplicate consecutive commands
setopt HIST_IGNORE_SPACE      # Do not record commands starting with a space
setopt SHARE_HISTORY          # Share history across all zsh sessions
setopt EXTENDED_HISTORY       # Record timestamp alongside each command

# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------
autoload -Uz compinit
compinit

# Case-insensitive tab completion
zstyle ':completion:*' matcher-list 'm:{a-z}={A-Z}'
# Show completion menu when there are 2 or more candidates
zstyle ':completion:*' menu select

# ---------------------------------------------------------------------------
# Key bindings  (emacs-style; change to `bindkey -v` for vi-style)
# ---------------------------------------------------------------------------
bindkey -e
bindkey '^[[A' history-search-backward   # Up arrow — search history
bindkey '^[[B' history-search-forward    # Down arrow — search history
bindkey '^[[H' beginning-of-line         # Home key
bindkey '^[[F' end-of-line               # End key

# ---------------------------------------------------------------------------
# zsh-syntax-highlighting
# Must be sourced AFTER other plugins and at the END of .zshrc
# ---------------------------------------------------------------------------
source /usr/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh

# ---------------------------------------------------------------------------
# zsh-autosuggestions
# ---------------------------------------------------------------------------
source /usr/share/zsh-autosuggestions/zsh-autosuggestions.zsh

ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE='fg=8'          # Gray suggestion text
ZSH_AUTOSUGGEST_STRATEGY=(history completion)   # Use history first, then completion

# Accept the full suggestion:
#   Right arrow  →  accept one character at a time (default)
#   End key      →  accept the full suggestion
#   Ctrl+E       →  alternative full-accept binding
bindkey '^e' autosuggest-accept
bindkey "${terminfo[kend]}" end-of-line          # End key for full accept

# ---------------------------------------------------------------------------
# Starship prompt  (must be the LAST line of .zshrc)
# ---------------------------------------------------------------------------
eval "$(starship init zsh)"
```

Apply immediately without reopening the terminal:
```zsh
source ~/.zshrc
```

---

## Step 7 — Configure Starship (Pastel-Powerline Preset)

### Option A — Generate via the embedded preset (recommended)

The preset files are **compiled into the starship binary** and require no internet access:

```zsh
mkdir -p ~/.config
starship preset pastel-powerline -o ~/.config/starship.toml
```

### Option B — Full manual config (use if starship is not yet installed)

Create `~/.config/starship.toml` with the following content. This is the full pastel-powerline
preset verbatim, reproduced here as a fallback:

```toml
# ~/.config/starship.toml — pastel-powerline preset

"$schema" = 'https://starship.rs/config-schema.json'

format = """
[░▒▓](#a3aed2)\
[  ](bg:#a3aed2 fg:#090c0c)\
[](bg:#769ff0 fg:#a3aed2)\
$directory\
[](fg:#769ff0 bg:#394260)\
$git_branch\
$git_status\
[](fg:#394260 bg:#212736)\
$nodejs\
$rust\
$golang\
$php\
[](fg:#212736 bg:#1d2230)\
$time\
[ ](bg:#1d2230)\
$line_break$character"""

[directory]
style = "fg:#e3e5e5 bg:#769ff0"
format = "[ $path ]($style)"
truncation_length = 3
truncation_symbol = "…/"

[directory.substitutions]
"Documents" = "󰈙 "
"Downloads" = " "
"Music" = " "
"Pictures" = " "

[git_branch]
symbol = ""
style = "bg:#394260"
format = '[[ $symbol $branch ](fg:#769ff0 bg:#394260)]($style)'

[git_status]
style = "bg:#394260"
format = '[[($all_status$ahead_behind )](fg:#769ff0 bg:#394260)]($style)'

[nodejs]
symbol = ""
style = "bg:#212736"
format = '[[ $symbol ($version) ](fg:#769ff0 bg:#212736)]($style)'

[rust]
symbol = ""
style = "bg:#212736"
format = '[[ $symbol ($version) ](fg:#769ff0 bg:#212736)]($style)'

[golang]
symbol = ""
style = "bg:#212736"
format = '[[ $symbol ($version) ](fg:#769ff0 bg:#212736)]($style)'

[php]
symbol = ""
style = "bg:#212736"
format = '[[ $symbol ($version) ](fg:#769ff0 bg:#212736)]($style)'

[time]
disabled = false
time_format = "%R"
style = "bg:#1d2230"
format = '[[  $time ](fg:#a0a9cb bg:#1d2230)]($style)'
```

---

## Step 8 — Reload and Verify

```zsh
source ~/.zshrc
```

**Verification checklist:**

| Check | Command | Expected result |
|-------|---------|-----------------|
| Shell is zsh | `echo $SHELL` | `/bin/zsh` |
| Starship is in PATH | `starship --version` | `starship 1.x.x` |
| Prompt renders glyphs | Open new terminal | Powerline arrows and icons visible |
| Syntax highlighting | Type `ls -la` | Command text is colored |
| Syntax error highlight | Type `lssss` | Command turns red/orange |
| Autosuggestions | Type a partial previous command | Gray suggestion appears |
| Accept suggestion | Press `→` or `Ctrl+E` | Suggestion is accepted |

---

## Troubleshooting

### Prompt shows garbled characters / boxes instead of arrows

The terminal font is not a Nerd Font, or Windows Terminal is not using the correct font face.

1. Open Windows Terminal → Settings → your OpenSUSE profile → Appearance
2. Set **Font face** to the Nerd Font you installed (e.g. `MesloLGS NF`)
3. Restart the terminal

### Syntax highlighting not working

Confirm the plugin was installed and the path exists:
```zsh
ls /usr/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh
```
If the file is missing, re-run Step 2–3. Also ensure the `source` line is present in `~/.zshrc`
and that it appears **after** `compinit`.

### Autosuggestions not appearing

```zsh
ls /usr/share/zsh-autosuggestions/zsh-autosuggestions.zsh
```
If missing, re-run Step 2–3. If the file exists but suggestions don't appear, check that your
`ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE` color is visible against your terminal background — change
`fg=8` to `fg=244` (medium gray) or `fg=blue` if needed.

### `starship: command not found`

If you installed via Option A/B (RPM), check:
```zsh
which starship || echo "not in PATH"
rpm -q starship       # Confirm the RPM is installed
```

If installed via Cargo (Option C), add the Cargo bin dir to PATH in `~/.zshrc`:
```zsh
export PATH="$HOME/.cargo/bin:$PATH"
```
Place this line **before** the `eval "$(starship init zsh)"` line.

### `zypper` cannot reach `download.opensuse.org`

Verify proxy settings are applied in the current session:
```zsh
echo $https_proxy
```
If empty, add to `/etc/sysconfig/proxy` (requires sudo):
```
PROXY_ENABLED="yes"
HTTP_PROXY="http://<host>:<port>"
HTTPS_PROXY="http://<host>:<port>"
NO_PROXY="localhost,127.0.0.1"
```
Then reload:
```zsh
sudo systemctl restart wickedd 2>/dev/null; source /etc/sysconfig/proxy
```
Or export variables directly in the current shell before running `zypper`.

### GPG key import failure for OBS repos

If `zypper refresh` fails with a GPG error, manually import the key:
```zsh
# For shells:/zsh-users:/zsh-syntax-highlighting
sudo rpm --import \
  https://download.opensuse.org/repositories/shells:/zsh-users:/zsh-syntax-highlighting/15.4/repodata/repomd.xml.key

# For shells:/zsh-users:/zsh-autosuggestions
sudo rpm --import \
  https://download.opensuse.org/repositories/shells:/zsh-users:/zsh-autosuggestions/15.4/repodata/repomd.xml.key
```

---

## Quick Reference — Installed Locations

| Item | Path |
|------|------|
| zsh binary | `/bin/zsh` |
| starship binary | `/usr/bin/starship` (RPM) or `~/.cargo/bin/starship` (Cargo) |
| zsh-syntax-highlighting script | `/usr/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh` |
| zsh-autosuggestions script | `/usr/share/zsh-autosuggestions/zsh-autosuggestions.zsh` |
| zsh config | `~/.zshrc` |
| Starship config | `~/.config/starship.toml` |
| zsh history | `~/.zsh_history` |
