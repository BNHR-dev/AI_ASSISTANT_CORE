# Exemple de configuration du portproxy Ollama côté host
# À adapter selon le runtime réel

netsh interface portproxy add v4tov4 `
    listenaddress=192.168.77.1 `
    listenport=12001 `
    connectaddress=127.0.0.1 `
    connectport=12000