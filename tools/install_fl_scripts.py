"""Copy the two FL-side scripts into FL Studio's user data folder (or remove them). Safe to run again.

  python tools\\install_fl_scripts.py            # install / update
  python tools\\install_fl_scripts.py --remove

  fl_script\\device_ClaudeBridge.py   -> <FL user data>\\Settings\\Hardware\\Claude Bridge\\
  fl_script\\Claude Notes.pyscript    -> <FL user data>\\Settings\\Piano roll scripts\\
<FL user data> is Documents\\Image-Line\\FL Studio unless config.json says otherwise ("fl_user_data").
After installing, pick "Claude Bridge" as the controller type in FL's MIDI settings (docs/SETUP.md).
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, 'server'))
import config  # noqa: E402

UD = config.get('fl_user_data')
FILES = [(os.path.join(HERE, 'fl_script', 'device_ClaudeBridge.py'),
          os.path.join(UD, 'Settings', 'Hardware', 'Claude Bridge', 'device_ClaudeBridge.py')),
         (os.path.join(HERE, 'fl_script', 'Claude Notes.pyscript'),
          os.path.join(UD, 'Settings', 'Piano roll scripts', 'Claude Notes.pyscript'))]

if '--remove' in sys.argv:
    for _, dst in FILES:
        if os.path.exists(dst):
            os.remove(dst)
            print('removed', dst)
else:
    for src, dst in FILES:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        print('installed', dst)
    print('Now (re)load the script in FL: Options > MIDI settings > Update MIDI scripts, or restart FL.')
