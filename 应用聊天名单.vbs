Option Explicit
Dim shell, files, root, script, command
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
script = files.BuildPath(root, "scripts\apply-access.ps1")
command = "powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -File """ & script & """"
shell.Run command, 0, False
