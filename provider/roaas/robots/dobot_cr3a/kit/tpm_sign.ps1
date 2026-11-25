# Signs a file with the recorder's TPM key (called by episode.py --signer tpm).
# Output: raw IEEE P1363 signature (r||s, 64 bytes) written to -OutFile.
# The private key is used inside the TPM and is never read by this script.

param(
    [Parameter(Mandatory = $true)][string]$KeyName,
    [Parameter(Mandatory = $true)][string]$InFile,
    [Parameter(Mandatory = $true)][string]$OutFile
)
$ErrorActionPreference = "Stop"

$provider = New-Object System.Security.Cryptography.CngProvider("Microsoft Platform Crypto Provider")
$key = [System.Security.Cryptography.CngKey]::Open($KeyName, $provider)
$ecdsa = New-Object System.Security.Cryptography.ECDsaCng($key)
$ecdsa.HashAlgorithm = [System.Security.Cryptography.CngAlgorithm]::Sha256
$data = [System.IO.File]::ReadAllBytes($InFile)
$sig = $ecdsa.SignData($data)
[System.IO.File]::WriteAllBytes($OutFile, $sig)
