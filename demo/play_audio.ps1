param([Parameter(Mandatory = $true)][string]$Path, [string]$ProgressPath, [string]$ControlPath)
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class PenguinAudio {
    [DllImport("winmm.dll", CharSet = CharSet.Unicode)]
    public static extern uint mciSendString(string command, StringBuilder result, uint length, IntPtr callback);
    [DllImport("winmm.dll", CharSet = CharSet.Unicode)]
    public static extern bool mciGetErrorString(uint code, StringBuilder result, uint length);
}
'@
function Invoke-PenguinAudio([string]$Command) {
    $audioResult = [PenguinAudio]::mciSendString($Command, $null, 0, [IntPtr]::Zero)
    if ($audioResult -ne 0) {
        $audioError = [System.Text.StringBuilder]::new(256)
        $null = [PenguinAudio]::mciGetErrorString($audioResult, $audioError, 256)
        throw $audioError.ToString()
    }
}
$audioPath = (Resolve-Path -LiteralPath $Path).Path
try {
    Invoke-PenguinAudio ('open "' + $audioPath + '" type mpegvideo alias penguin')
    Invoke-PenguinAudio 'setaudio penguin volume to 650'
    if ($ProgressPath) {
        Invoke-PenguinAudio 'set penguin time format milliseconds'
        Invoke-PenguinAudio 'play penguin'
        $result = [System.Text.StringBuilder]::new(64)
        $lastCommand = ''
        $commandError = ''
        while ($true) {
            if ($ControlPath -and [System.IO.File]::Exists($ControlPath)) {
                try {
                    $request = [System.IO.File]::ReadAllText($ControlPath) | ConvertFrom-Json
                    if ($request.id -and $request.id -ne $lastCommand) {
                        $lastCommand = $request.id
                        $commandError = ''
                        try {
                            switch ($request.action) {
                                'pause' { Invoke-PenguinAudio 'pause penguin' }
                                'resume' { Invoke-PenguinAudio 'resume penguin' }
                                default { throw 'Unknown playback command' }
                            }
                        } catch {
                            $commandError = $_.Exception.Message
                        }
                    }
                } catch {
                    # Retry a command file that was being replaced during this read.
                }
            }
            $null = $result.Clear()
            $modeResult = [PenguinAudio]::mciSendString('status penguin mode', $result, 64, [IntPtr]::Zero)
            $mode = $result.ToString()
            if ($modeResult -ne 0 -or $mode -notin @('playing', 'paused')) { break }
            $null = $result.Clear()
            if ([PenguinAudio]::mciSendString('status penguin position', $result, 64, [IntPtr]::Zero) -eq 0) {
                [System.IO.File]::WriteAllText($ProgressPath, $result.ToString())
            }
            if ($ControlPath) {
                $status = @{ id = $lastCommand; mode = $mode; error = $commandError } | ConvertTo-Json -Compress
                [System.IO.File]::WriteAllText(($ControlPath + '.state'), $status, [System.Text.UTF8Encoding]::new($false))
            }
            $null = $result.Clear()
            Start-Sleep -Milliseconds 125
        }
    } else {
        Invoke-PenguinAudio 'play penguin wait'
    }
} finally {
    $null = [PenguinAudio]::mciSendString('close penguin', $null, 0, [IntPtr]::Zero)
}
