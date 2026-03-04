# Oh My Zsh — Offline Installation Guide

This guide covers installing Oh My Zsh on a machine **without internet access**, using a locally cloned repo.

---

## Prerequisites

On the **online machine** (where you have this repo), gather everything you need and transfer it to the offline machine (via USB drive, `scp` over LAN, etc.).

### What to download on the online machine

```bash
# 1. You already have this repo cloned. Good.

# 2. Clone the Spaceship theme
git clone https://github.com/spaceship-prompt/spaceship-prompt.git

# 3. Clone zsh-autosuggestions plugin
git clone https://github.com/zsh-users/zsh-autosuggestions.git

# 4. Clone zsh-syntax-highlighting plugin
git clone https://github.com/zsh-users/zsh-syntax-highlighting.git
```

Transfer all four directories to the offline machine:
- `ohmyzsh/` (this repo)
- `spaceship-prompt/`
- `zsh-autosuggestions/`
- `zsh-syntax-highlighting/`

---

## Step 1: Ensure `zsh` is installed on the offline machine

```bash
zsh --version
```

If not installed, you'll need to install it from a local `.deb`/`.rpm` or from source. On Debian/Ubuntu (with local `.deb` files):

```bash
sudo dpkg -i zsh_*.deb
```

---

## Step 2: Install Oh My Zsh (offline, no git clone)

The standard `install.sh` tries to `git clone` from GitHub. For offline installation, **copy the repo directly** instead:

```bash
# Copy the ohmyzsh repo to ~/.oh-my-zsh
cp -r /path/to/ohmyzsh ~/.oh-my-zsh

# Backup existing .zshrc if any
[ -f ~/.zshrc ] && cp ~/.zshrc ~/.zshrc.pre-oh-my-zsh

# Set zsh as default shell (optional, requires password)
chsh -s $(which zsh)
```

> **NOTE**: Replace `/path/to/ohmyzsh` with the actual path where you transferred the repo on the offline machine.

---

## Step 3: Install the Spaceship theme (offline)

The Spaceship theme is **NOT** bundled with Oh My Zsh (it's not in the `themes/` directory). You must install it into the custom themes folder:

```bash
# Copy the spaceship-prompt repo into custom themes
cp -r /path/to/spaceship-prompt ~/.oh-my-zsh/custom/themes/spaceship-prompt

# Create the required symlink
ln -sf ~/.oh-my-zsh/custom/themes/spaceship-prompt/spaceship.zsh-theme \
       ~/.oh-my-zsh/custom/themes/spaceship.zsh-theme
```

---

## Step 4: Install the external plugins (offline)

Both `zsh-autosuggestions` and `zsh-syntax-highlighting` are **NOT** bundled with Oh My Zsh. Install them into the custom plugins folder:

```bash
# zsh-autosuggestions
cp -r /path/to/zsh-autosuggestions ~/.oh-my-zsh/custom/plugins/zsh-autosuggestions

# zsh-syntax-highlighting
cp -r /path/to/zsh-syntax-highlighting ~/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting
```

---

## Step 5: Create the `.zshrc` file

Create `~/.zshrc` with the adjusted configuration below. Key path adjustments made:

- The `stat -f '%Sm' -t '%j'` syntax is **macOS-only**. Replaced with Linux-compatible `stat` syntax.
- `export ZSH="$HOME/.oh-my-zsh"` — already correct, uses `$HOME` so it's portable.
- SSH key path adjusted to a placeholder — change it to your actual key path.

```bash
cat > ~/.zshrc << 'ZSHRC_EOF'
# ===========================================================================
# Performance optimizations
# ===========================================================================
DISABLE_AUTO_UPDATE="true"
DISABLE_MAGIC_FUNCTIONS="true"
DISABLE_COMPFIX="true"

# Cache completions aggressively
# Rebuild compdump only once per day for faster shell startup
autoload -Uz compinit
if [[ -n ~/.zcompdump(#qN.mh+24) ]]; then
    compinit
else
    compinit -C
fi

# ===========================================================================
# Oh My Zsh path
# ===========================================================================
export ZSH="$HOME/.oh-my-zsh"

# ===========================================================================
# Theme config
# ===========================================================================
ZSH_THEME="spaceship"

# Spaceship prompt settings
SPACESHIP_PROMPT_ASYNC=true
SPACESHIP_PROMPT_ADD_NEWLINE=true
SPACESHIP_CHAR_SYMBOL="⚡"

# Minimal spaceship sections for performance
SPACESHIP_PROMPT_ORDER=(
  time
  user
  dir
  git
  line_sep
  char
)

# ===========================================================================
# Plugins
# ===========================================================================
# NOTE: zsh-syntax-highlighting MUST be last in the list
plugins=(
  git
  zsh-autosuggestions
  zsh-syntax-highlighting
)

# ===========================================================================
# Source Oh My Zsh
# ===========================================================================
source $ZSH/oh-my-zsh.sh

# ===========================================================================
# Autosuggest settings
# ===========================================================================
ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE="fg=#663399,standout"
ZSH_AUTOSUGGEST_BUFFER_MAX_SIZE="20"
ZSH_AUTOSUGGEST_USE_ASYNC=1

# ===========================================================================
# Alias expansion (globalias)
# ===========================================================================
globalias() {
   if [[ $LBUFFER =~ '[a-zA-Z0-9]+$' ]]; then
       zle _expand_alias
       zle expand-word
   fi
   zle self-insert
}
zle -N globalias
bindkey " " globalias
bindkey "^[[Z" magic-space
bindkey -M isearch " " magic-space

# ===========================================================================
# Lazy load SSH agent
# ===========================================================================
function _load_ssh_agent() {
    if [ -z "$SSH_AUTH_SOCK" ]; then
        eval "$(ssh-agent -s)" > /dev/null
        # CHANGE THIS to your actual SSH key path
        ssh-add ~/.ssh/id_ed25519 2>/dev/null
    fi
}
autoload -U add-zsh-hook
add-zsh-hook precmd _load_ssh_agent

# ===========================================================================
# Source aliases last
# ===========================================================================
[ -f ~/.zsh_aliases ] && source ~/.zsh_aliases
ZSHRC_EOF
```

---

## Step 6: Start using it

```bash
# Launch zsh
zsh

# Or log out and log back in if you changed your default shell with chsh
```

---

## Configuration Explained (line by line)

### Performance Optimizations

| Setting | What it does |
|---|---|
| `DISABLE_AUTO_UPDATE="true"` | Prevents Oh My Zsh from checking GitHub for updates on every shell start. **Essential for offline use** — without this, every shell startup would fail trying to reach GitHub. |
| `DISABLE_MAGIC_FUNCTIONS="true"` | Disables Oh My Zsh's URL magic quoting feature (auto-escaping of URLs when pasting). This can cause noticeable lag when pasting text, so disabling it speeds up paste operations. |
| `DISABLE_COMPFIX="true"` | Skips the security check that scans completion directories for "insecure" permissions. This check runs `compaudit` on every startup and can be slow, especially on networked filesystems. |

### Completion Caching

```zsh
autoload -Uz compinit
if [[ -n ~/.zcompdump(#qN.mh+24) ]]; then
    compinit
else
    compinit -C
fi
```

- `autoload -Uz compinit` — Lazily loads the `compinit` function (zsh completion system initializer). `-U` suppresses alias expansion, `-z` forces zsh-style loading.
- The `if` block checks if `~/.zcompdump` (the cached completions database) is **older than 24 hours**. If so, it rebuilds it (slow, ~200ms). Otherwise it uses `-C` to skip the security check and load the cached version instantly (~20ms).
- **Original config issue**: Your colleague's version used `stat -f '%Sm' -t '%j'` which is **macOS BSD `stat` syntax**. On Linux, this doesn't work. The adjusted version uses zsh's native glob qualifiers `(#qN.mh+24)` which is portable across all zsh installations.

### Oh My Zsh Path

```zsh
export ZSH="$HOME/.oh-my-zsh"
```

Tells Oh My Zsh where its files live. `$HOME` expands to the current user's home directory (e.g., `/home/praveen`), making it portable across users.

### Theme Configuration

```zsh
ZSH_THEME="spaceship"
```

Sets the prompt theme to **Spaceship** — a minimalist, async-capable prompt that shows contextual info (git branch, user, directory, etc.). **Not bundled with Oh My Zsh** — must be installed separately (Step 3 above).

```zsh
SPACESHIP_PROMPT_ASYNC=true
```
Enables async rendering — git status and other slow operations run in the background so the prompt appears instantly.

```zsh
SPACESHIP_PROMPT_ADD_NEWLINE=true
```
Adds a blank line before each prompt, improving visual separation between commands.

```zsh
SPACESHIP_CHAR_SYMBOL="⚡"
```
Changes the prompt character from the default `➜` to `⚡`.

```zsh
SPACESHIP_PROMPT_ORDER=(time user dir git line_sep char)
```
Only shows these 6 sections (out of 30+ available), significantly reducing prompt rendering time:
- `time` — Current time
- `user` — Username (shown when relevant, e.g. SSH sessions)
- `dir` — Current working directory
- `git` — Git branch and status
- `line_sep` — Line break (prompt input on a new line)
- `char` — The prompt symbol (`⚡`)

### Plugins

```zsh
plugins=(git zsh-autosuggestions zsh-syntax-highlighting)
```

| Plugin | What it does | Bundled? |
|---|---|---|
| `git` | Adds 150+ git aliases (`gst`=`git status`, `gco`=`git checkout`, etc.) and git prompt info functions. | **Yes** — included with Oh My Zsh |
| `zsh-autosuggestions` | Shows grayed-out command suggestions based on your history as you type. Press `→` to accept. | **No** — install separately (Step 4) |
| `zsh-syntax-highlighting` | Highlights valid commands in green and invalid ones in red as you type. **Must be last in the plugins list** so it can process the full input buffer after all other plugins. | **No** — install separately (Step 4) |

### Autosuggest Settings

```zsh
ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE="fg=#663399,standout"
```
Colors the suggestion text with hex color `#663399` (Rebecca Purple) and uses `standout` mode (reverse video). Change the hex color to your preference.

```zsh
ZSH_AUTOSUGGEST_BUFFER_MAX_SIZE="20"
```
Only shows suggestions when the current input is ≤20 characters. This prevents expensive history lookups for long commands.

```zsh
ZSH_AUTOSUGGEST_USE_ASYNC=1
```
Fetches suggestions asynchronously so typing never blocks waiting for a suggestion.

### Globalias (Inline Alias Expansion)

```zsh
globalias() {
   if [[ $LBUFFER =~ '[a-zA-Z0-9]+$' ]]; then
       zle _expand_alias
       zle expand-word
   fi
   zle self-insert
}
zle -N globalias
bindkey " " globalias
bindkey "^[[Z" magic-space
bindkey -M isearch " " magic-space
```

This creates a ZLE (Zsh Line Editor) widget that **expands aliases inline when you press Space**:

- `$LBUFFER` contains text to the left of the cursor.
- If it ends with a word, `_expand_alias` tries to expand it as an alias, and `expand-word` performs additional expansion.
- `zle self-insert` then inserts the space character.
- `bindkey " " globalias` — binds Space key to trigger the expansion.
- `bindkey "^[[Z" magic-space` — binds Shift+Tab to `magic-space` (expands history references like `!!`).
- `bindkey -M isearch " " magic-space` — in incremental search mode, Space performs history expansion instead of expanding aliases.

**Example**: If you have `alias gs='git status'`, typing `gs` then Space will replace it with `git status ` inline.

### Lazy SSH Agent

```zsh
function _load_ssh_agent() {
    if [ -z "$SSH_AUTH_SOCK" ]; then
        eval "$(ssh-agent -s)" > /dev/null
        ssh-add ~/.ssh/id_ed25519 2>/dev/null
    fi
}
autoload -U add-zsh-hook
add-zsh-hook precmd _load_ssh_agent
```

- `precmd` hook runs **before each prompt display** (i.e., before every command).
- First time: `$SSH_AUTH_SOCK` is empty, so it starts `ssh-agent` and loads your key.
- Subsequent times: `$SSH_AUTH_SOCK` is already set, so the function returns immediately (no overhead).
- **Adjust the key path** (`~/.ssh/id_ed25519`) to match your actual SSH key filename. Your colleague had `~/.ssh/id_github_sign_and_auth` — change this to whatever key exists on your machine.

### Source Aliases

```zsh
[ -f ~/.zsh_aliases ] && source ~/.zsh_aliases
```

If `~/.zsh_aliases` exists, source it. This lets you keep custom aliases in a separate file. Create it with your aliases:

```bash
# Example ~/.zsh_aliases
alias ll='ls -la'
alias gs='git status'
```

---

## Quick Reference: Complete Commands (copy-paste ready)

Run these on the **offline machine** after transferring all repos. Adjust `/path/to/` to where you placed the files:

```bash
# Install oh-my-zsh
cp -r /path/to/ohmyzsh ~/.oh-my-zsh

# Install spaceship theme
cp -r /path/to/spaceship-prompt ~/.oh-my-zsh/custom/themes/spaceship-prompt
ln -sf ~/.oh-my-zsh/custom/themes/spaceship-prompt/spaceship.zsh-theme \
       ~/.oh-my-zsh/custom/themes/spaceship.zsh-theme

# Install plugins
cp -r /path/to/zsh-autosuggestions ~/.oh-my-zsh/custom/plugins/zsh-autosuggestions
cp -r /path/to/zsh-syntax-highlighting ~/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting

# Backup old .zshrc and create new one
[ -f ~/.zshrc ] && cp ~/.zshrc ~/.zshrc.pre-oh-my-zsh
# Then create ~/.zshrc with the content from Step 5 above

# Change default shell to zsh
chsh -s $(which zsh)

# Start zsh
zsh
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `command not found: compdef` | Run `chmod -R go-w ~/.oh-my-zsh` to fix permissions |
| Spaceship theme not loading | Verify the symlink: `ls -la ~/.oh-my-zsh/custom/themes/spaceship.zsh-theme` |
| Autosuggestions not working | Verify the plugin dir: `ls ~/.oh-my-zsh/custom/plugins/zsh-autosuggestions/` |
| `stat: invalid option -- 'f'` | You're using the macOS `stat` syntax on Linux — use the adjusted `.zshrc` from Step 5 |
| `⚡` symbol shows as `?` | Your terminal font doesn't support Unicode — install a Nerd Font or change `SPACESHIP_CHAR_SYMBOL` to `>` |
