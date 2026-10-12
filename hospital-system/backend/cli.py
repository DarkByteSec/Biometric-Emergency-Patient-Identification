import argparse, getpass, os, uuid
from argon2 import PasswordHasher
from app import create_app
from app.db import connection, now

def main():
    p=argparse.ArgumentParser(description='Hospital bootstrap and optional OpenCV inspection')
    sub=p.add_subparsers(dest='cmd',required=True)
    b=sub.add_parser('bootstrap-operator',help='Create the first named admin/operator; no defaults')
    b.add_argument('--email'); b.add_argument('--role',choices=['admin','operator'],default='admin')
    sub.add_parser('inspect-opencv',help='Report whether optional OpenCV contrib LBPH support is installed')
    args=p.parse_args()
    if args.cmd=='inspect-opencv':
        try:
            import cv2; print({'opencv_version':cv2.__version__,'lbph_available':hasattr(cv2,'face') and hasattr(cv2.face,'LBPHFaceRecognizer_create')})
        except Exception as e: print({'installed':False,'reason':type(e).__name__})
        return
    app=create_app(); email=(args.email or input('Named operator email: ').strip()).lower(); password=getpass.getpass('Password (not displayed): ')
    if not email or len(password)<12: raise SystemExit('Email is required and password must be at least 12 characters')
    ph=PasswordHasher(); uid=str(uuid.uuid4())
    with app.app_context():
        with connection() as c:
            if c.execute('SELECT 1 FROM hospital_users LIMIT 1').fetchone(): raise SystemExit('Bootstrap refused: a user already exists')
            c.execute('INSERT INTO hospital_users (id,email,password_hash,role,is_active,created_at,updated_at) VALUES (?,?,?,?,?,?,?)',(uid,email,ph.hash(password),args.role,True,now(),now()))
    print('Created named Hospital '+args.role+' account: '+email)
if __name__=='__main__': main()
