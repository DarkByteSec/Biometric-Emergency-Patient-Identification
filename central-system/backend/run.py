from app import create_app
from app.cli import register_cli

app = create_app()
register_cli(app)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
