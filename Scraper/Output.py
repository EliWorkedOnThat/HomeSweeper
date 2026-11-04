#This file will act as the output handler for each scraper
#Imports
from io import BytesIO
from pathlib import Path
from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from rich import print

#Global Variables
OUTPUT_FILE = Path(__file__).resolve().parent.parent / "Output" / "listings.xlsx"
SHEET_NAME = "Listings"
BASE_COLUMNS = [
    "site", "upid", "title", "price", "currency", "seller", "phone",
    "published_date", "published_time", "url", "description", "images",
]
MAX_CELL_LENGTH = 32767 #Excel's limit per cell, longer text makes Excel say the file is damaged when it's opened

#Helper function to open the workbook or create it with headers if it doesn't exist yet
def open_workbook():
    if OUTPUT_FILE.exists():
        workbook = load_workbook(OUTPUT_FILE)
        return workbook, workbook[SHEET_NAME]

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    del workbook["Sheet"] #Remove the default empty sheet openpyxl creates
    sheet = workbook.create_sheet(SHEET_NAME)
    append_row(sheet, BASE_COLUMNS)
    return workbook, sheet

#Helper function to make a scraped value safe for an Excel cell: openpyxl refuses control characters like \x0b (it raises
#IllegalCharacterError, which would break the whole file), so they are removed, and text is cut to Excel's cell limit
def clean_cell(value):
    if not isinstance(value, str):
        return value
    return ILLEGAL_CHARACTERS_RE.sub("", value)[:MAX_CELL_LENGTH]

#Helper function to add one row to a sheet as plain values: openpyxl turns any text starting with "=" into a live formula,
#and sellers write their own titles and descriptions, so "=HYPERLINK(...)" would become a working link in the agent's Excel.
#Those cells are switched back to text, so Excel shows exactly what the seller wrote and never runs it (we never write real formulas)
def append_row(sheet, values):
    sheet.append(values)
    for cell in sheet[sheet.max_row]:
        if cell.data_type == "f":
            cell.data_type = "s"

#Helper function to turn one listing dict into a flat row (attributes get their own columns), every value and column name cleaned
def flatten_listing(listing, site):
    row = {key: listing.get(key) for key in BASE_COLUMNS}
    row["site"] = site
    row["images"] = "\n".join(listing.get("images") or [])
    row.update(listing.get("attributes") or {})
    return {clean_cell(str(key)): clean_cell(value) for key, value in row.items()}

#Function to append scraped listings to the excel file, skipping ones already saved
def save_listings(listings, site):
    workbook, sheet = open_workbook()
    headers = [str(cell.value) for cell in sheet[1]]

    #Collect (site, upid) pairs already in the sheet so we don't save duplicates
    site_col = headers.index("site")
    upid_col = headers.index("upid")
    saved = {
        (str(row[site_col]), str(row[upid_col]))
        for row in sheet.iter_rows(min_row=2, values_only=True)
    }

    added = 0
    for listing in listings:
        row = flatten_listing(listing, site)
        if (site, str(row["upid"])) in saved:
            continue

        #Add a new column for any attribute we haven't seen before
        for key in row:
            if key not in headers:
                headers.append(key)
                sheet.cell(row=1, column=len(headers), value=key)

        append_row(sheet, [row.get(header) for header in headers])
        saved.add((site, str(row["upid"])))
        added += 1

    try:
        workbook.save(OUTPUT_FILE)
    except PermissionError:
        print(f"[red]Could not save {OUTPUT_FILE} — close it in Excel and try again.[/red]")
        return 0

    print(f"[green]Saved {added} new listings to {OUTPUT_FILE} ({len(listings) - added} already existed)[/green]")
    return added

#Function to build a fresh workbook in memory from database listings (used by the UI download, so an open Excel file can't block it)
def build_workbook(listings):
    rows = [flatten_listing(listing, listing.get("site")) for listing in listings]

    headers = list(BASE_COLUMNS)
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_NAME #type: ignore
    append_row(sheet, headers)
    for row in rows:
        append_row(sheet, [row.get(header) for header in headers])

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
