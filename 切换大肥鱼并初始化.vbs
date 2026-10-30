Option Explicit
Dim files, shell, root, q, command
Set files = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = files.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
q = Chr(34)
command = q & root & "\.venv\Scripts\python.exe" & q & " " & q & root & "\scripts\switch_remote_persona.py" & q & " dafeiyu"
shell.Run command, 0, False
