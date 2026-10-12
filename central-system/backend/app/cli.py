import click
from flask import current_app

from .db import execute, now, query, uid
from .services import encrypt_id, hash_password, id_hmac


def register_cli(app):
    @app.cli.command("bootstrap-admin")
    @click.option("--email", prompt=True)
    @click.option("--role", type=click.Choice(["admin", "auditor"]), prompt=True)
    def bootstrap(email, role):
        """Create a named admin interactively; password is never stored or defaulted."""
        email = email.strip().lower()
        if query("SELECT id FROM admin_users WHERE email=?", (email,), one=True):
            raise click.ClickException("Email already exists")
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
        if len(password) < 12:
            raise click.ClickException("Password must be at least 12 characters")
        ident = uid()
        execute(
            "INSERT INTO admin_users VALUES(?,?,?,?,?,?,?,?)",
            (ident, email, hash_password(password), role, True, now(), now(), None),
        )
        click.echo(
            "Admin created. Store the password in your approved password manager."
        )

    @app.cli.command("seed-demo")
    def seed_demo():
        """Seed clearly synthetic records only when explicitly enabled in development."""
        if (
            current_app.config["APP_ENV"] != "development"
            or not current_app.config["ALLOW_DEMO_SEED"]
        ):
            raise click.ClickException(
                "Refusing seed: require APP_ENV=development and ALLOW_DEMO_SEED=true"
            )
        if query("SELECT id FROM citizens LIMIT 1", one=True):
            raise click.ClickException("Database is not empty; refusing seed")
        for i in range(3):
            nid = f"DEMO-{1000 + i}"
            execute(
                "INSERT INTO citizens VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uid(),
                    encrypt_id(nid),
                    id_hmac(nid),
                    nid[-4:],
                    f"Demo{i + 1}",
                    "Synthetic",
                    "1990-01-01",
                    "unknown",
                    "unknown",
                    now(),
                    now(),
                    None,
                ),
            )
        click.echo(
            "Seeded synthetic-only citizen records. No credentials were created."
        )
