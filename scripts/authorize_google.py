"""Ejecutar UNA VEZ en estación con navegador, nunca en el timer del servidor."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from google_auth_oauthlib.flow import InstalledAppFlow
from crt_comments.google_sync import atomic_token, SCOPES
p = argparse.ArgumentParser()
p.add_argument('--client', required=True, help='JSON de cliente OAuth Desktop de Google Cloud')
p.add_argument('--output', required=True, help='Destino privado token.json')
a = p.parse_args()
flow = InstalledAppFlow.from_client_secrets_file(a.client, SCOPES)
creds = flow.run_local_server(port=0, access_type='offline', prompt='consent')
atomic_token(a.output, creds.to_json())
print('Autorización guardada; transferir el JSON al servidor por canal seguro.')
