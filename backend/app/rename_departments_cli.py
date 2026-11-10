"""Переименование папок департаментов по списку банка (узбекские названия, ноябрь 2026)."""
import argparse

from app.services import catalog
from app.services.store import close_store

# Старое название папки -> название из перечня департаментов банка.
RENAMES = {
    "Sun'iy intellekt departamenti": 'Sun’iy intellekt departamenti',
    'O’rta biznes markazi': 'O‘rta biznes markazi',
    "G'aznachilik departamenti": 'G‘aznachilik departamenti',
    'Sustainable finance departamenti': 'Sustainable Finance departamenti',
    "Ma'muriy xo'jalik departamenti": 'Ma’muriy xo‘jalik departamenti',
    'Департамент одобрения кредитов': 'Kreditlarni ma’qullash departamenti',
    'Muammoli kredit qarzdorliklari bilan ishlash departamenti': 'Kredit qarzdorliklari bilan ishlash departamenti',
    'Департамент цифрового бизнеса': 'Raqamli biznes departamenti',
    'Департамент розничного бизнеса': 'Chakana biznes departamenti',
    'Департамент малого и микро бизнеса': 'Mikro va kichik biznes departamenti',
    'Xaridlarni tashkil etish xizmati': 'Xaridlarni tashkil etish va monitoring departamenti',
    "Ma'lumotlarni boshqarish markazi": 'Ma’lumotlarni boshqarish markazi',
    'Operatsion departament': 'Operatsion departamenti',
    'Департамент поддержки систем банковских карт': 'Bank kartalari tizimlarini qo‘llab-quvvatlash departamenti',
    'Контактный центр': 'Aloqa markazi',
    'Risk-menejment departamenti': 'Risk menejment departamenti',
    'Komplaens-nazorat departamenti': 'Komplayens-nazorat departamenti',
    "Aholini moliyaviy qo'llab-quvvatlash va tadbirkorlikka jalb qilish departamenti":
        'Aholini moliyaviy qo‘llab-quvvatlash va tadbirkorlikka jalb qilish departamenti',
    'Premium markazi': 'Premium banking markazi',
}


def main():
    parser = argparse.ArgumentParser(description='Переименование папок департаментов по перечню банка')
    parser.add_argument('--apply', action='store_true', help='Без флага только показывает, что будет изменено')
    args = parser.parse_args()
    try:
        items = catalog.snapshot({}).items
        roots = {item.id for item in items if item.kind == 'space' and item.parentId is None}
        departments = [item for item in items if item.kind == 'space' and item.parentId in roots]
        found = set()
        for item in departments:
            new = RENAMES.get(item.title)
            if new is None:
                continue
            found.add(item.title)
            print(f'{item.id}: «{item.title}» -> «{new}»')
            if args.apply:
                catalog.update_item(item.id, {'title': new}, {})
        for old in RENAMES:
            if old not in found:
                print(f'не найдено (уже переименовано или другое название): «{old}»')
        print(('переименовано' if args.apply else 'будет переименовано') + f': {len(found)} из {len(RENAMES)}')
    finally:
        close_store()


if __name__ == '__main__':
    main()
