; VideoEditor installer (Inno Setup 6). Built by packaging\windows\build.ps1, which passes
; AppVersion, StageDir, GpuDir and OutputDir. Installs per user: no administrator needed.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef StageDir
  #define StageDir "..\..\build\windows\stage"
#endif
#ifndef GpuDir
  #define GpuDir "..\..\build\windows\gpu"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\build\windows"
#endif

#define Launcher "{app}\python\pythonw.exe"

[Setup]
AppId={{6B0E5B8C-3F2A-4E7D-9C1B-8A4D2E6F7A10}
AppName=VideoEditor
AppVersion={#AppVersion}
AppVerName=VideoEditor {#AppVersion}
AppPublisher=otavioxfran
AppPublisherURL=https://github.com/otavioxfran/VideoEditor
AppSupportURL=https://github.com/otavioxfran/VideoEditor/issues
DefaultDirName={localappdata}\Programs\VideoEditor
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=VideoEditor-Setup-{#AppVersion}
SetupIconFile=VideoEditor.ico
UninstallDisplayIcon={app}\VideoEditor.ico
UninstallDisplayName=VideoEditor
LicenseFile=..\..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=no
CloseApplications=no

[Languages]
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Types]
Name: "full"; Description: "Completa: com aceleração para placas NVIDIA (recomendada)"
Name: "compact"; Description: "Sem placa NVIDIA: menor, transcrição pela CPU"
Name: "custom"; Description: "Personalizada"; Flags: iscustom

[Components]
Name: "app"; Description: "VideoEditor (Python, FFmpeg e o editor)"; Types: full compact custom; Flags: fixed
Name: "gpu"; Description: "Aceleração para placas NVIDIA (cerca de 700 MB)"; Types: full

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion; Components: app
Source: "{#GpuDir}\ctranslate2\*"; DestDir: "{app}\python\Lib\site-packages\ctranslate2"; Flags: ignoreversion; Components: gpu
Source: "VideoEditor.ico"; DestDir: "{app}"; Components: app

[Icons]
Name: "{autoprograms}\VideoEditor"; Filename: "{#Launcher}"; Parameters: """{app}\app\launcher.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\VideoEditor.ico"; Comment: "Abrir o estúdio de corte"
Name: "{autoprograms}\Encerrar o VideoEditor"; Filename: "{#Launcher}"; Parameters: """{app}\app\launcher.py"" --stop"; WorkingDir: "{app}\app"; IconFilename: "{app}\VideoEditor.ico"
Name: "{autodesktop}\VideoEditor"; Filename: "{#Launcher}"; Parameters: """{app}\app\launcher.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\VideoEditor.ico"; Tasks: desktopicon

[Run]
Filename: "{#Launcher}"; Parameters: """{app}\app\launcher.py"""; WorkingDir: "{app}\app"; Description: "Abrir o VideoEditor agora"; Flags: postinstall nowait skipifsilent

[InstallDelete]
; An update replaces the program files; cuts and settings live in %LOCALAPPDATA%\VideoEditor.
Type: filesandordirs; Name: "{app}\app"

[UninstallRun]
Filename: "{#Launcher}"; Parameters: """{app}\app\launcher.py"" --stop"; WorkingDir: "{app}\app"; Flags: runhidden waituntilterminated; RunOnceId: "StopStudio"

[UninstallDelete]
; Python writes bytecode caches next to the installed files.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\app"

[Messages]
pt.FinishedLabel=O VideoEditor foi instalado. Seus cortes e configurações ficam na sua pasta de usuário (AppData\Local\VideoEditor) e não são apagados se você desinstalar.
