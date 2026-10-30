"""Native file and folder dialogs, opened by the local server on the user's own desktop.

The Studio is a browser page, and a browser never reveals a file's real path. The server
runs on the same machine, so it opens the operating system's own dialog instead and
returns the path the person chose (or None if they cancelled).
"""
import os
import shutil
import subprocess
import sys

TIMEOUT = 600

_WINDOWS_FILE = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true; ShowInTaskbar = $false}
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = 'Escolha a gravação'
$dialog.Filter = 'Vídeos (*.mp4;*.mov;*.mkv)|*.mp4;*.mov;*.mkv'
if ($env:PICKER_START -and (Test-Path $env:PICKER_START)) { $dialog.InitialDirectory = $env:PICKER_START }
if ($dialog.ShowDialog($owner) -eq 'OK') { $dialog.FileName }
"""

_WINDOWS_FOLDER = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true; ShowInTaskbar = $false}
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Escolha a pasta onde ficam suas gravações'
if ($env:PICKER_START -and (Test-Path $env:PICKER_START)) { $dialog.SelectedPath = $env:PICKER_START }
if ($dialog.ShowDialog($owner) -eq 'OK') { $dialog.SelectedPath }
"""

_TK = r"""
import sys, tkinter
from tkinter import filedialog
root = tkinter.Tk(); root.withdraw(); root.attributes('-topmost', True)
start = sys.argv[2] or None
if sys.argv[1] == 'file':
    value = filedialog.askopenfilename(initialdir=start, title='Escolha a gravação',
        filetypes=[('Vídeos', '*.mp4 *.mov *.mkv'), ('Todos', '*.*')])
else:
    value = filedialog.askdirectory(initialdir=start, title='Escolha a pasta das gravações')
sys.stdout.write(value or '')
"""


class PickerUnavailable(RuntimeError):
    pass


def _run(command, env=None):
    result = subprocess.run(command, capture_output=True, timeout=TIMEOUT, env=env,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    value = result.stdout.decode('utf-8', 'replace').strip()
    return value or None


def _pick(kind, start):
    start = str(start or '')
    if sys.platform == 'win32':
        script = _WINDOWS_FILE if kind == 'file' else _WINDOWS_FOLDER
        env = dict(os.environ, PICKER_START=start)
        return _run(['powershell', '-NoProfile', '-NonInteractive', '-STA', '-Command', script], env)
    if sys.platform == 'darwin':
        what = 'choose file of type {"mp4", "mov", "mkv", "public.movie"}' if kind == 'file' else 'choose folder'
        return _run(['osascript', '-e', f'POSIX path of ({what} with prompt "VideoEditor")'])
    if shutil.which('zenity'):
        command = ['zenity', '--file-selection', '--title=VideoEditor']
        if kind == 'folder':
            command.append('--directory')
        else:
            command.append('--file-filter=Vídeos | *.mp4 *.mov *.mkv *.MP4 *.MOV *.MKV')
        return _run(command)
    try:
        return _run([sys.executable, '-c', _TK, kind, start])
    except OSError as error:
        raise PickerUnavailable('Não há seletor de arquivos neste sistema; digite o caminho.') from error


def pick_file(start=None):
    return _pick('file', start)


def pick_folder(start=None):
    return _pick('folder', start)
