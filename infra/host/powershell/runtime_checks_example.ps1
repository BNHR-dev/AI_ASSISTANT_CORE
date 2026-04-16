# Vérifications utiles côté host

docker ps
netsh interface portproxy show v4tov4
Invoke-WebRequest http://192.168.77.1:12001/api/tags
Get-NetFirewallRule | Where-Object DisplayName -like "AICORE *" | Format-Table DisplayName, Enabled, Profile, Direction, Action