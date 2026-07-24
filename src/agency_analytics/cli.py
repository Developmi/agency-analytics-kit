import argparse
import subprocess
import sys
from pathlib import Path

CLIENTS_DIR = Path(__file__).resolve().parent.parent.parent / "clients"


def cmd_pipeline(args):
    print("Corriendo pipeline local (sin Docker)...")
    client_files = list(CLIENTS_DIR.glob("*.yml"))
    if not client_files:
        print("No se encontraron archivos de clientes en", CLIENTS_DIR)
        return 1
    success = 0
    failed = 0
    for cf in client_files:
        client_id = cf.stem
        print(f"  Procesando cliente: {client_id}")
        result = subprocess.run(
            [sys.executable, "-m", "agency_analytics.cli", "dlt", "--client", client_id],
            capture_output=True,
            text=True,
        )
        print(f"    dlt: {'OK' if result.returncode == 0 else 'FAIL'}")
        if result.returncode != 0:
            print(f"    stderr: {result.stderr.strip()}")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agency_analytics.cli",
                "dbt",
                "--command",
                "run",
                "--client",
                client_id,
            ],
            capture_output=True,
            text=True,
        )
        print(f"    dbt: {'OK' if result.returncode == 0 else 'FAIL'}")
        if result.returncode != 0:
            print(f"    stderr: {result.stderr.strip()}")
        if result.returncode == 0:
            success += 1
        else:
            failed += 1
    print(f"Pipeline finalizado: {success} exitos, {failed} fallos")
    return 0 if failed == 0 else 1


def cmd_dbt(args):
    cmd = ["dbt", args.command]
    if args.client:
        cmd.extend(["--vars", f'{{"client_id": "{args.client}"}}'])
    print(f"Ejecutando: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    return result.returncode


def cmd_dlt(args):
    connector = args.connector
    client = args.client
    script = CLIENTS_DIR.parent / "src" / "connectors" / f"run_{connector}.py"
    if not script.exists():
        print(f"Error: script dlt no encontrado: {script}")
        return 1
    print(f"Ejecutando: {sys.executable} {script} --client {client}")
    result = subprocess.run([sys.executable, str(script), "--client", client])
    return result.returncode


def cli():
    parser = argparse.ArgumentParser(
        prog="agency-analytics",
        description="Agency Analytics Kit CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_pipeline = subparsers.add_parser("pipeline", help="Corre el pipeline completo localmente")
    p_pipeline.set_defaults(func=cmd_pipeline)

    p_dbt = subparsers.add_parser("dbt", help="Ejecuta comandos dbt")
    p_dbt.add_argument("--command", default="run", help="Comando dbt (run, test, build, etc.)")
    p_dbt.add_argument("--client", default=None, help="Cliente para filtrar modelos via tag")
    p_dbt.set_defaults(func=cmd_dbt)

    p_dlt = subparsers.add_parser("dlt", help="Corre un extractor dlt")
    p_dlt.add_argument(
        "--connector",
        required=True,
        help=(
            "Nombre del conector: meta, tiktok, google, facebook, instagram, "
            "tiktok_organic, youtube, pinterest, ga4, gtm"
        ),
    )
    p_dlt.add_argument("--client", required=True, help="ID del cliente")
    p_dlt.set_defaults(func=cmd_dlt)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(cli())
