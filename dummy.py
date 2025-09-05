import camelot

# Specify the path to the PDF file
file_path = "/home/diksha-encureitlp43/Documents/EncureIT/Project/fastapi-file-pipeline/input/JAMNAGAR MUNICIPAL CORPORATION.pdf"

# Read the tables from the PDF
# total_tables = 0
# for flavor in ["lattice", "stream"]:
#     tables = camelot.read_pdf(file_path, pages="all", flavor=flavor)
#     print(f"Flavor '{flavor}' found {tables.n} tables")
#     total_tables += tables.n

# print(f"\nTotal tables found (both flavors): {total_tables}")


tables = camelot.read_pdf(file_path, pages='88-96', flavor='lattice')  # pages='1' can be changed to '1-3' etc.

# Check how many tables were found
print(f"Total tables found: {len(tables)}")

# Loop through and print each table as a DataFrame
for i, table in enumerate(tables):
    print(f"\nTable {i+1}")
    print(table.df)  # table.df is a pandas DataFrame"

# Optionally, save tables to CSV
for i, table in enumerate(tables):
    table.to_csv(f"table_{i+1}.csv")