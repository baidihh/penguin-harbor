$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Devices.Bluetooth.BluetoothDevice, Windows.Devices.Bluetooth, ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Bluetooth.BluetoothLEDevice, Windows.Devices.Bluetooth, ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Enumeration.DeviceInformation, Windows.Devices.Enumeration, ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Bluetooth.Rfcomm.RfcommDeviceService, Windows.Devices.Bluetooth, ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceService, Windows.Devices.Bluetooth, ContentType=WindowsRuntime] | Out-Null

$script:AsTask = [System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1

function Await-Result($Operation, [Type]$ResultType, [int]$TimeoutSeconds = 12) {
    $task = $script:AsTask.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    if (-not $task.Wait($TimeoutSeconds * 1000)) {
        $Operation.Cancel()
        throw "Bluetooth query timed out after $TimeoutSeconds seconds"
    }
    return ,$task.GetAwaiter().GetResult()
}

foreach ($transport in @('Classic', 'LE')) {
    try {
        if ($transport -eq 'Classic') {
            $selector = [Windows.Devices.Bluetooth.BluetoothDevice]::GetDeviceSelectorFromPairingState($true)
        } else {
            $selector = [Windows.Devices.Bluetooth.BluetoothLEDevice]::GetDeviceSelectorFromPairingState($true)
        }
        $devices = Await-Result ([Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync($selector)) ([Windows.Devices.Enumeration.DeviceInformationCollection])
        $earbuds = @($devices | Where-Object { $_.Name -match 'OPPO|Enco' })
        [pscustomobject]@{ Stage='PairedDevices'; Transport=$transport; MatchedCount=$earbuds.Count } | ConvertTo-Json -Compress

        foreach ($info in $earbuds) {
            $device = $null
            try {
                if ($transport -eq 'Classic') {
                    $device = Await-Result ([Windows.Devices.Bluetooth.BluetoothDevice]::FromIdAsync($info.Id)) ([Windows.Devices.Bluetooth.BluetoothDevice])
                    if ($null -eq $device) { throw 'BluetoothDevice.FromIdAsync returned null' }
                    [pscustomobject]@{ Stage='Connection'; Transport=$transport; Name=$device.Name; Status=[string]$device.ConnectionStatus } | ConvertTo-Json -Compress
                    $services = Await-Result ($device.GetRfcommServicesAsync([Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached)) ([Windows.Devices.Bluetooth.Rfcomm.RfcommDeviceServicesResult])
                    [pscustomobject]@{ Stage='Services'; Transport=$transport; Status=[string]$services.Error; UUIDs=@($services.Services | ForEach-Object { [string]$_.ServiceId.Uuid }); Channels=@($services.Services | ForEach-Object { [pscustomobject]@{ UUID=[string]$_.ServiceId.Uuid; Port=$_.ConnectionServiceName } }) } | ConvertTo-Json -Compress -Depth 4
                    foreach ($service in $services.Services) { $service.Dispose() }
                } else {
                    $device = Await-Result ([Windows.Devices.Bluetooth.BluetoothLEDevice]::FromIdAsync($info.Id)) ([Windows.Devices.Bluetooth.BluetoothLEDevice])
                    if ($null -eq $device) { throw 'BluetoothLEDevice.FromIdAsync returned null' }
                    [pscustomobject]@{ Stage='Connection'; Transport=$transport; Name=$device.Name; Status=[string]$device.ConnectionStatus } | ConvertTo-Json -Compress
                    $services = Await-Result ($device.GetGattServicesAsync([Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceServicesResult])
                    [pscustomobject]@{ Stage='Services'; Transport=$transport; Status=[string]$services.Status; UUIDs=@($services.Services | ForEach-Object { [string]$_.Uuid }) } | ConvertTo-Json -Compress
                    foreach ($service in $services.Services) {
                        try {
                            $characteristics = Await-Result ($service.GetCharacteristicsAsync([Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattCharacteristicsResult])
                            $items = @($characteristics.Characteristics | ForEach-Object {
                                [pscustomobject]@{ UUID=[string]$_.Uuid; Properties=[string]$_.CharacteristicProperties }
                            })
                            [pscustomobject]@{ Stage='Characteristics'; Service=[string]$service.Uuid; Status=[string]$characteristics.Status; Items=$items } | ConvertTo-Json -Compress -Depth 5
                        } finally { $service.Dispose() }
                    }
                }
            } catch {
                [pscustomobject]@{ Stage='DeviceError'; Transport=$transport; Name=$info.Name; Error=$_.Exception.Message } | ConvertTo-Json -Compress
            } finally {
                if ($null -ne $device) { $device.Dispose() }
            }
        }
    } catch {
        [pscustomobject]@{ Stage='EnumerationError'; Transport=$transport; Error=$_.Exception.Message } | ConvertTo-Json -Compress
    }
}
