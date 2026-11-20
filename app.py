"""Launch Wire Judge Live."""

APP_VERSION = '1.2.0'


def main():
    from wirejudge.app import main as run_app

    return run_app(APP_VERSION)


if __name__ == '__main__':
    raise SystemExit(main())
