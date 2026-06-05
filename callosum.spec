# -*- mode: python ; coding: utf-8 -*-

import os
import sys

block_cipher = None

# We need to explicitly include chromadb's hidden imports since it dynamically loads many things
hiddenimports = [
    'chromadb',
    'chromadb.api',
    'chromadb.telemetry.posthog',
    'chromadb.utils.embedding_functions',
    'chromadb.app',
    'chromadb.config',
    'chromadb.db',
    'chromadb.db.impl',
    'chromadb.db.impl.sqlite',
    'chromadb.segment',
    'chromadb.segment.impl',
    'chromadb.segment.impl.vector',
    'chromadb.segment.impl.vector.local_hnsw',
    'sqlite3',
    'hnswlib',
    'onnxruntime',
    'posthog',
    'uvicorn',
    'fastapi',
    'pydantic',
    'pystray',
    'PIL',
]

a = Analysis(
    ['callosum/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[
        # Include our built-in linguistic data
        ('callosum/data/*.json', 'callosum/data'),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='callosum',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
