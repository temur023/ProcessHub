"""Операторское первичное назначение администратора существующему аккаунту Hub."""
import argparse

from app.services.store import close_store, get_store


def main():
    parser = argparse.ArgumentParser(description='Назначение главного администратора Process Hub')
    parser.add_argument('action', choices=['grant', 'revoke'])
    parser.add_argument('--email', required=True)
    parser.add_argument('--reason', required=True, help='Основание назначения или отзыва для аудита')
    args = parser.parse_args()
    reason = args.reason.strip()
    if not reason:
        parser.error('Укажите основание действия')
    try:
        user = get_store().set_platform_admin(args.email.strip().lower(), args.action == 'grant', reason)
        print(f'{user.email}: главный администратор={user.isAdmin}; user_id={user.id}')
    except ValueError as error:
        parser.exit(1, str(error) + '\n')
    finally:
        close_store()


if __name__ == '__main__':
    main()
