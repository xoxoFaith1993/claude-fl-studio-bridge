# Security

**What the bridge can do:** it runs Python code inside FL Studio, sent over two local virtual MIDI ports. That's
how it works: `fl_exec` and every other tool send code to the "Claude Bridge" MIDI script. Anything that can send
SysEx to the "Claude to FL" port can run code in FL with your user's rights.

What that means for you:
- Only connect the bridge's virtual ports to the bridge. Don't route hardware or network MIDI (rtpMIDI and
  similar) into "Claude to FL".
- Claude Code asks before running tools unless you allowed them. Allow the `fl-studio` tools only as far as
  you trust what you ask for.
- The bridge never sends anything over the internet. The optional `fl_read_window` talks only to your local
  Ollama (`ollama_host`, default `localhost`).

## Reporting a vulnerability
Please **don't open a public issue** for security problems. Use GitHub's private
[security advisory](https://github.com/xoxoFaith1993/claude-fl-studio-bridge/security/advisories/new) form
instead. We'll answer as soon as we can. This is a volunteer project with no fixed response time.
