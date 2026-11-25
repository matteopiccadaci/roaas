# Creates the recorder's signing key INSIDE the Windows TPM (run once on the recording PC).
#
# - Key: ECDSA P-256 named "DobotEpisodeRecorder", created by the "Microsoft Platform Crypto Provider"
#   (the TPM 2.0), signing only, non-exportable: the private key never leaves the TPM.
# - The PUBLIC key is exported to keys\recorder_tpm_pub.blob (Windows ECC public blob);
#   the kit converts it to PEM.
# - Uses the CNG API directly (no certificate enrollment).
#
# Usage (PowerShell, from the kit folder, normal user is enough):
#   powershell -NoProfile -ExecutionPolicy Bypass -File tpm_setup.ps1

param(
    [string]$KeyName = "DobotEpisodeRecorder",
    [string]$OutDir = "keys"
)
$ErrorActionPreference = "Stop"
$provider = New-Object System.Security.Cryptography.CngProvider("Microsoft Platform Crypto Provider")

if ([System.Security.Cryptography.CngKey]::Exists($KeyName, $provider)) {
    Write-Output "Key '$KeyName' already exists in the TPM: reusing it."
    $key = [System.Security.Cryptography.CngKey]::Open($KeyName, $provider)
} else {
    $params = New-Object System.Security.Cryptography.CngKeyCreationParameters
    $params.Provider = $provider
    $params.KeyUsage = [System.Security.Cryptography.CngKeyUsages]::Signing
    $params.ExportPolicy = [System.Security.Cryptography.CngExportPolicies]::None
    $key = [System.Security.Cryptography.CngKey]::Create(
        [System.Security.Cryptography.CngAlgorithm]::ECDsaP256, $KeyName, $params)
    Write-Output "Key '$KeyName' created in the TPM."
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$outPath = Join-Path (Resolve-Path $OutDir) "recorder_tpm_pub.blob"
$blob = $key.Export([System.Security.Cryptography.CngKeyBlobFormat]::EccPublicBlob)
[System.IO.File]::WriteAllBytes($outPath, $blob)
Set-Content -Path (Join-Path $OutDir "tpm_keyname.txt") -Value $KeyName -Encoding ascii

Write-Output "Key storage provider : $($key.Provider.Provider)"
Write-Output "Algorithm            : $($key.Algorithm.Algorithm)"
Write-Output "Export policy        : $($key.ExportPolicy)"
Write-Output "Public key written to: $outPath"
if ($key.Provider.Provider -ne "Microsoft Platform Crypto Provider") {
    Write-Warning "The key is NOT in the TPM provider."
}
