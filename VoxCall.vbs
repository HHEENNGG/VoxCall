' VoxCall launcher: starts the GUI with pythonw.exe, so no console window appears.
' Optional argument: path to another script (for debugging).
Option Explicit

Dim fso, sh, base, script, exe, logFile, fh
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

base = fso.GetParentFolderName(WScript.ScriptFullName)
script = base & "\main.py"
If WScript.Arguments.Count > 0 Then script = WScript.Arguments(0)

exe = base & "\runtime\python313\python\pythonw.exe"
If Not fso.FileExists(exe) Then
    exe = base & "\.venv\Scripts\pythonw.exe"
End If

If Not fso.FileExists(exe) Then
    MsgBox "VoxCall: runtime not found (runtime\python313 or .venv)." & vbCrLf & vbCrLf & _
           "Please run:  install.ps1 -Portable", 16, "VoxCall"
    WScript.Quit 1
End If

On Error Resume Next
Set fh = fso.OpenTextFile(base & "\logs\launcher.log", 8, True)
If Err.Number = 0 Then
    fh.WriteLine "run " & exe & " " & script
    fh.Close
End If
Err.Clear
On Error Goto 0

sh.CurrentDirectory = base
sh.Run """" & exe & """ """ & script & """", 0, False
