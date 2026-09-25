# Build with: python -m PyInstaller --noconfirm frincoms.spec
import sys


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == 'darwin':
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Frincoms', console=False)
    coll = COLLECT(exe, a.binaries, a.datas, name='Frincoms')
    app = BUNDLE(
        coll,
        name='Frincoms.app',
        bundle_identifier='com.frincoms.app',
        info_plist={'NSMicrophoneUsageDescription': 'Frincoms uses your microphone for voice calls.'},
    )
elif sys.platform == 'win32':
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name='Frincoms', console=False, onefile=True,
    )
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Frincoms', console=False)
    coll = COLLECT(exe, a.binaries, a.datas, name='Frincoms')
