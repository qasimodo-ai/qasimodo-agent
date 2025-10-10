# Code Signing Setup

To sign executables in GitHub Actions, you need to configure the following secrets in your repository.

## Windows Code Signing

**Required secrets:**
- `WINDOWS_CERTIFICATE`: PFX certificate encoded in base64
- `WINDOWS_CERTIFICATE_PASSWORD`: Certificate password

**How to obtain a certificate:**
1. Purchase a Code Signing certificate from a trusted CA (e.g., DigiCert, Sectigo)
2. Export the certificate in PFX format with private key
3. Convert to base64:
   ```bash
   # Linux/macOS
   base64 -i certificate.pfx -o certificate.txt

   # Windows PowerShell
   [Convert]::ToBase64String([IO.File]::ReadAllBytes("certificate.pfx")) | Out-File certificate.txt
   ```
4. Add `WINDOWS_CERTIFICATE` with the content of certificate.txt
5. Add `WINDOWS_CERTIFICATE_PASSWORD` with the PFX password

## macOS Code Signing and Notarization

**Required secrets:**
- `MACOS_CERTIFICATE`: p12 certificate encoded in base64
- `MACOS_CERTIFICATE_PASSWORD`: p12 certificate password
- `APPLE_ID`: Your Apple ID email
- `APPLE_PASSWORD`: App-specific password (not your account password!)
- `APPLE_TEAM_ID`: Your Team ID (10 alphanumeric characters)

**How to obtain certificates:**
1. Enroll in Apple Developer Program ($99/year)
2. Create a "Developer ID Application" certificate on developer.apple.com
3. Download and import into Keychain Access
4. Export certificate as p12:
   - Open Keychain Access
   - Find "Developer ID Application" certificate
   - Right-click → Export
   - Save as .p12 with password
5. Convert to base64:
   ```bash
   base64 -i certificate.p12 -o certificate.txt
   ```
6. Find your Team ID:
   - Go to developer.apple.com/account
   - Membership → Team ID
7. Create App-specific password:
   - Go to appleid.apple.com
   - Sign in → Security → App-specific passwords
   - Generate a new password

**Configure secrets:**
- `MACOS_CERTIFICATE`: Content of certificate.txt
- `MACOS_CERTIFICATE_PASSWORD`: p12 password
- `APPLE_ID`: Your Apple ID email
- `APPLE_PASSWORD`: Generated app-specific password
- `APPLE_TEAM_ID`: Team ID (e.g., "AB12CD34EF")

## Configure Secrets

1. Go to GitHub repository → Settings → Secrets and variables → Actions
2. Click "New repository secret"
3. Add each secret with correct name and value

## Notes

- **Signing is optional**: Workflows work without configured secrets
- **Without signing on Windows**: Users will see "SmartScreen" warning
- **Without signing on macOS**: Users must right-click and "Open" on first run
- **Linux**: Does not require code signing
- **Costs**:
  - Windows certificate: ~$100-400/year
  - Apple Developer: $99/year
  - Approval time: 1-3 business days

## Local Testing

To test signing locally before committing:

**Windows:**
```powershell
signtool sign /f certificate.pfx /p password /tr http://timestamp.digicert.com /td sha256 /fd sha256 dist/pyinstaller-test.exe
```

**macOS:**
```bash
codesign --force --deep --sign "Developer ID Application" --options runtime --timestamp dist/pyinstaller-test
xcrun notarytool submit dist/app.zip --apple-id you@email.com --password xxxx-xxxx-xxxx-xxxx --team-id TEAMID --wait
```
