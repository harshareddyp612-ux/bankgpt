import os

from .app import app

if __name__ == "__main__":
    port = int(os.environ.get("MOCK_APP_PORT", "5055"))
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
