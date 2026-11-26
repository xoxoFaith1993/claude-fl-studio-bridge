"""Connect this bridge to Claude Code (or undo it). Safe to run again.

  python tools\\connect_claude.py                 # register the MCP server + add the banner hooks
  python tools\\connect_claude.py --no-hooks      # MCP server only
  python tools\\connect_claude.py --remove        # undo both
  python tools\\connect_claude.py --print         # only print what it would do

MCP server: runs `claude mcp add --scope user <name> -- <python> <server.py>` (needs the `claude` command on PATH;
otherwise it prints the command for you to run).
Hooks (FL bridge only): adds a Stop and a UserPromptSubmit hook to ~/.claude/settings.json that run
`overlay.py release`, so the "hands off" banner fades as soon as Claude's turn ends. The file is backed up first
(settings.json.bak-<bridge>) and existing hooks are kept.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = 'fl-studio'                       # MCP server name
SERVER = os.path.join(HERE, 'server', 'server.py')
OVERLAY = os.path.join(HERE, 'server', 'overlay.py')
HOOK_EVENTS = ('Stop', 'UserPromptSubmit')
SETTINGS = os.path.join(os.path.expanduser('~'), '.claude', 'settings.json')


def python_exe():
    """The bundled Python next to the install (installer), else the one running this script."""
    bundled = os.path.join(HERE, 'python', 'python.exe')
    return bundled if os.path.isfile(bundled) else sys.executable


def hook_command():
    return '"%s" "%s" release' % (python_exe().replace('\\', '/'), OVERLAY.replace('\\', '/'))


def claude_cli():
    return shutil.which('claude') or shutil.which('claude.cmd') or shutil.which('claude.exe')


def mcp(remove, dry):
    cmd = ['mcp', 'remove', '--scope', 'user', NAME] if remove else \
        ['mcp', 'add', '--scope', 'user', NAME, '--', python_exe(), SERVER]
    cli = claude_cli()
    shown = 'claude ' + ' '.join('"%s"' % c if ' ' in c else c for c in cmd)
    if dry or not cli:
        print(('Run this to %s the MCP server:\n  ' % ('remove' if remove else 'add')) + shown)
        if not cli and not dry:
            print('(the "claude" command was not found on PATH - install Claude Code first)')
            if not remove:   # leave the command where the installer can show it
                with open(os.path.join(HERE, 'CONNECT_CLAUDE.txt'), 'w', encoding='utf-8') as f:
                    f.write('The claude command was not found, so the tools are not registered yet.\n'
                            'Install Claude Code (https://claude.com/claude-code), then run this in a terminal:\n\n  '
                            + shown + '\n\nThen start a new Claude Code session.\n')
        return
    # only ever replace or remove a registration that points at THIS copy (never someone's other install)
    cur = subprocess.run([cli, 'mcp', 'get', NAME], capture_output=True, text=True)
    exists = cur.returncode == 0 and NAME in (cur.stdout or '')
    ours = os.path.normcase(SERVER) in os.path.normcase(cur.stdout or '').replace('/', '\\')
    if exists and not ours:
        print('An MCP server named "%s" already exists and points somewhere else; leaving it alone.' % NAME)
        print('To switch to this copy, remove it first:  claude mcp remove --scope user %s' % NAME)
        return
    if remove and not exists:
        print('no MCP server "%s" registered' % NAME)
        return
    if not remove and exists:   # ours already: re-register cleanly
        subprocess.run([cli, 'mcp', 'remove', '--scope', 'user', NAME], capture_output=True)
    r = subprocess.run([cli] + cmd, capture_output=True, text=True)
    print((r.stdout or r.stderr).strip() or ('done: ' + shown))


def _is_ours(h):
    """Only hooks that point at THIS copy's overlay.py (other installs and your own hooks are left alone)."""
    cmd = h.get('command', '').replace('\\', '/').lower()
    return OVERLAY.replace('\\', '/').lower() in cmd and 'release' in cmd


def hooks(remove, dry):
    data = {}
    if os.path.isfile(SETTINGS):
        with open(SETTINGS, encoding='utf-8') as f:
            data = json.load(f)
    all_hooks = data.setdefault('hooks', {})
    changed = False
    for ev in HOOK_EVENTS:
        groups = all_hooks.setdefault(ev, [])
        for g in groups:                                  # drop old copies of our hook
            before = len(g.get('hooks', []))
            g['hooks'] = [h for h in g.get('hooks', []) if not _is_ours(h)]
            changed |= len(g['hooks']) != before
        groups[:] = [g for g in groups if g.get('hooks')]
        if not remove:
            groups.append({'hooks': [{'type': 'command', 'command': hook_command() + ' 2>/dev/null || true',
                                      'timeout': 10}]})
            changed = True
        if not groups:
            del all_hooks[ev]
    if not all_hooks:
        data.pop('hooks', None)
    if dry:
        print('Would write hooks to %s:\n%s' % (SETTINGS, json.dumps(data.get('hooks', {}), indent=1)))
        return
    if not changed:
        print('hooks: nothing to change')
        return
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    if os.path.isfile(SETTINGS):
        shutil.copyfile(SETTINGS, SETTINGS + '.bak-' + NAME)
    with open(SETTINGS, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    print('hooks %s in %s' % ('removed' if remove else 'added', SETTINGS))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--remove', action='store_true')
    ap.add_argument('--no-hooks', action='store_true')
    ap.add_argument('--no-mcp', action='store_true')
    ap.add_argument('--print', action='store_true', dest='dry')
    a = ap.parse_args()
    if not a.no_mcp:
        mcp(a.remove, a.dry)
    if not a.no_hooks:
        hooks(a.remove, a.dry)
    print('Restart Claude Code (a new session) to load the tools.')
