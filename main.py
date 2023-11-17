"""Something!"""
from __future__ import annotations
from dataclasses import dataclass, fields
from collections.abc import Iterable, Iterator
from typing import List, Optional, Tuple, Dict
from pathlib import Path
import argparse
import logging
from datetime import datetime
import requests

from google.oauth2 import service_account
import google.auth.transport.requests


def _setup_logging(log_filename: str, directory: str = "") -> logging.Logger:
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

    c_handler = logging.StreamHandler()
    current_time = datetime.now().strftime("%Y%m%d%H%M%S")
    f_handler = logging.FileHandler(f"{directory}/{current_time}_{log_filename}")
    c_handler.setLevel(logging.INFO)
    f_handler.setLevel(logging.INFO)

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    c_handler.setFormatter(formatter)
    f_handler.setFormatter(formatter)

    logger.addHandler(c_handler)
    logger.addHandler(f_handler)
    return logger


@dataclass
class IndustryIdentifier:  # pylint: disable=missing-class-docstring
    isbn_10: Optional[str] = None
    isbn_13: Optional[str] = None

    def __init__(self, industry_identifiers: Optional[List[Dict[str, str]]]):
        if industry_identifiers is None:
            return
        for identifier in industry_identifiers:
            if identifier["type"] == "ISBN_10":
                self.isbn_10 = identifier["identifier"]
            elif identifier["type"] == "ISBN_13":
                self.isbn_13 = identifier["identifier"]


@dataclass
class ReadingMode:  # pylint: disable=missing-class-docstring
    text: Optional[bool] = None
    image: Optional[bool] = None

    def __init__(self, reading_modes: Optional[Dict[str, bool]]):
        if reading_modes is None:
            return
        if "text" in reading_modes:
            self.text = reading_modes["text"]
        if "image" in reading_modes:
            self.image = reading_modes["image"]


@dataclass
class ImageLink:  # pylint: disable=missing-class-docstring
    thumbnail: Optional[str] = None
    small_thumbnail: Optional[str] = None

    def __init__(self, image_links: Optional[Dict[str, str]]):
        if image_links is None:
            return
        if "thumbnail" in image_links:
            self.thumbnail = image_links["thumbnail"]
        if "small_thumbnail" in image_links:
            self.small_thumbnail = image_links["small_thumbnail"]


@dataclass
class Book:
    # pylint: disable=missing-class-docstring
    # pylint: disable=too-many-instance-attributes
    # pylint: disable=invalid-name
    title: str
    authors: List[str]
    publisher: Optional[str] = None
    publishedDate: Optional[str] = None
    description: Optional[str] = None
    industryIdentifiers: Optional[IndustryIdentifier] = None
    readingModes: Optional[ReadingMode] = None
    pageCount: Optional[int] = None
    printType: Optional[str] = None
    categories: Optional[List[str]] = None
    averageRating: Optional[float] = None
    ratingsCount: Optional[int] = None
    maturityRating: Optional[str] = None
    imageLinks: Optional[ImageLink] = None
    language: Optional[str] = None
    previewLink: Optional[str] = None

    @staticmethod
    def from_google_book_api(data: Dict) -> Book:
        """Return an instance of Book object from data retrieved from Google Book API

        * Ignoring keys:
          "allowAnonLogging", "contentVersion", "panelizationSummary",
          "infoLink", "canonicalVolumeLink"
        """

        d = {field.name: data.get(field.name, None) for field in fields(Book)}
        d["industryIdentifiers"] = IndustryIdentifier(d["industryIdentifiers"])
        d["readingModes"] = ReadingMode(d["readingModes"])
        d["imageLinks"] = ImageLink(d["imageLinks"])
        return Book(**d)


def _get_next_line(lines: Iterable[str]) -> Optional[str]:
    try:
        line = next(lines).strip()
        while not line:  # Skip blank lines
            line = next(lines).strip()
        return line
    except StopIteration:
        return None


def _parse_book_titles_file(file_path: Path) -> List[Book]:
    with open(file_path, "r", encoding="utf-8") as file:
        lines = iter(file.readlines())
    result = []
    while True:
        title = _get_next_line(lines)
        if title is None:
            break
        authors_line = _get_next_line(lines)
        authors = list(set(authors_line.split("; ")))
        result.append(Book(title=title, authors=authors))
    return result


def _fetch_book_info(
    service_account_file_path: Path, books: List[Book]
) -> Iterator[Tuple[int, Optional[Book]]]:
    _connect_timeout = 30
    _read_timeout = 30

    # Load the service account credentials and authenticate
    scopes = ["https://www.googleapis.com/auth/books"]
    credentials = service_account.Credentials.from_service_account_file(
        service_account_file_path, scopes=scopes
    )
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)

    for book in books:
        # Query
        query = f"{book.title} {' '.join(book.authors)}"
        url = f"https://www.googleapis.com/books/v1/volumes?q={query}"
        response = requests.get(
            url=url,
            headers={"Authorization": "Bearer " + credentials.token},
            timeout=(_connect_timeout, _read_timeout),
        )

        # Parse respose
        # TODO: if response was invalide, the input book is being returned (but should not).
        if response.status_code == 200:
            data = response.json()
            if "items" not in data or len(data["items"]) == 0:
                # "No results found "
                yield response.status_code, book
            if "items" in data and len(data["items"]) == 1:
                book_data = data["items"][0]["volumeInfo"]
                yield response.status_code, Book.from_google_book_api(book_data)
            if "items" in data and len(data["items"]) > 1:
                # TODO: more than one book is found. give option to user to choose?
                book_data = data["items"][0]["volumeInfo"]
                yield response.status_code, Book.from_google_book_api(book_data)
        else:
            # "Failed to fetch data"
            yield response.status_code, book


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--credential-file",
        type=str,
        required=True,
        help="Google Service Account cridential file (json)",
    )
    parser.add_argument(
        "--book-list-file",
        type=str,
        required=True,
        help="Path to a file that lists books by title-author",
    )
    args = parser.parse_args()
    return args


def main():  # pylint: disable=missing-function-docstring
    logger = _setup_logging(log_filename="listing_books.log", directory="logs")

    arguments = _parse_arguments()
    book_list_file = Path(arguments.book_list_file)
    credential_file = Path(arguments.credential_file)

    book_titles = _parse_book_titles_file(book_list_file)
    logger.info("Number of titles in the book list file: %d", len(book_titles))

    start_time = datetime.now()
    for response_status_code, book_out in _fetch_book_info(
        credential_file, book_titles
    ):
        if response_status_code == 200 and book_out is not None:
            logger.info("FOUND: %s", book_out.title)
        else:
            logger.info("NOT FOUND: %s", book_out.title)
    elapsed_time = datetime.now() - start_time
    logger.info("process finished in: %f seconds", elapsed_time.total_seconds())


if __name__ == "__main__":
    main()
