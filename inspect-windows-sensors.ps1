$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Devices.Enumeration.DeviceInformation, Windows.Devices.Enumeration, ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Sensors.Accelerometer, Windows.Devices, ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Sensors.Gyrometer, Windows.Devices, ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Sensors.OrientationSensor, Windows.Devices, ContentType=WindowsRuntime] | Out-Null
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1

$selectors = @{
    Accelerometer = [Windows.Devices.Sensors.Accelerometer]::GetDeviceSelector([Windows.Devices.Sensors.AccelerometerReadingType]::Standard)
    Gyrometer = [Windows.Devices.Sensors.Gyrometer]::GetDeviceSelector()
    Orientation = [Windows.Devices.Sensors.OrientationSensor]::GetDeviceSelector([Windows.Devices.Sensors.SensorReadingType]::Absolute)
}
foreach ($entry in $selectors.GetEnumerator()) {
    $operation = [Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync($entry.Value)
    $task = $asTask.MakeGenericMethod([Windows.Devices.Enumeration.DeviceInformationCollection]).Invoke($null, @($operation))
    if (-not $task.Wait(10000)) {
        $operation.Cancel()
        throw 'Windows sensor enumeration timed out'
    }
    $devices = $task.GetAwaiter().GetResult()
    [pscustomobject]@{
        SensorType = $entry.Key
        InterfaceCount = $devices.Count
        OppoMatches = @($devices | Where-Object { $_.Name -match 'OPPO|Enco' } | ForEach-Object { $_.Name })
    } | ConvertTo-Json -Compress
}
