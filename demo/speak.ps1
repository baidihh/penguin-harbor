$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Speech
$liliSpeaker = [System.Speech.Synthesis.SpeechSynthesizer]::new()
$liliVoice = $liliSpeaker.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -eq 'zh-CN' } | Select-Object -First 1
if ($liliVoice) { $liliSpeaker.SelectVoice($liliVoice.VoiceInfo.Name) }
$liliSpeaker.Rate = -1
$liliSpeaker.Volume = 65
$liliText = [Console]::In.ReadToEnd()
$liliSpeaker.Speak($liliText)
$liliSpeaker.Dispose()
