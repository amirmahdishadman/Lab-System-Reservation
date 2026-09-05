# Lab System Reservation

A small Django web application for reserving lab computers. Accounts are created by an administrator; there is no public registration. Users can manage their profile image and their own future reservations, while administrators manage all users, systems, and bookings.

## Start with Docker

1. Copy the example configuration and replace every placeholder secret:

   ```sh
   cp .env.example .env
   ```

2. Build and start the application:

   ```sh
   docker compose up --build -d
   ```

3. Create the first administrator:

   ```sh
   docker compose exec web python manage.py createsuperuser
   ```

4. Open `http://localhost:8000`, sign in, then use **Systems** and **Users** in the navigation to configure the lab.

Use `docker compose logs -f web` to view application logs. Stop the app with `docker compose down`; the database and uploaded profile images remain in named volumes.

## Configuration

`APP_TIME_ZONE` controls the single timezone shown throughout the calendar and defaults to `Asia/Tehran`. Set `DJANGO_ALLOWED_HOSTS` to the hostname or IP used to access the server. For HTTPS deployments, set `DJANGO_CSRF_TRUSTED_ORIGINS` to the full origin and `DJANGO_SECURE_COOKIES=1` behind a TLS reverse proxy.

Do not deploy with the example database passwords or Django secret key. The `/health/` endpoint reports whether the application can reach its database.

## Behavior

- A reservation must have an end later than its start and a non-admin user cannot create one in the past.
- Two active reservations cannot overlap for the same system. Back-to-back reservations are allowed.
- Conflict checks run inside a transaction while locking the system row, so simultaneous booking requests cannot both win.
- Users can edit or cancel only their own future bookings. Administrators can manage any booking.
- Deactivation preserves existing history while preventing new reservations.

## Tests

Run the regular suite in an isolated container (the MariaDB-specific concurrency test is skipped):

```sh
docker compose run --no-deps --rm -e DB_NAME= web python manage.py test
```

To include the concurrency test, run the Compose test container using a MariaDB account that may create and remove a temporary `test_lab_reservations` database. The application account intentionally does not have that production permission. For a local installation, that command is:

```sh
docker compose run --rm -e DB_USER=root -e DB_PASSWORD='<your DB_ROOT_PASSWORD>' web python manage.py test
```

The entrypoint applies database migrations and collects static files whenever the web container starts.

## Backup and restore

Create a database dump:

```sh
docker compose exec db mariadb-dump -u root -p lab_reservations > lab-reservations.sql
```

Profile images live in the Compose `media_data` volume and should be backed up separately. Restore into an empty database with the MariaDB client, restore the media volume, then start the web service so migrations can run.

## Updating

Back up the database and media volume, pull the updated source, then run:

```sh
docker compose up --build -d
```
