"""Check the whole FL Studio bridge setup and print what's wrong and how to fix it. Changes nothing.

  python tools\\doctor.py          # plain report
  python tools\\doctor.py --json   # the same as JSON (paste it into an issue - check it for private paths first)
"""
import os
import sys

os.environ.setdefault('FL_OVERLAY', '0')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'server'))
import doctor  # noqa: E402

if __name__ == '__main__':
    res = doctor.diagnose()
    if '--json' in sys.argv:
        import json
        print(json.dumps(res, indent=1))
    else:
        print(doctor.text_report(res))
    sys.exit(1 if any(r['result'] == 'fail' for r in res['checks']) else 0)
