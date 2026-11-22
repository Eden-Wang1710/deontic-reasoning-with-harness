# Searching the document collection

Two commands are on your PATH. They are the only way to read the document collection.

- `search "<query>"` returns the 10 most relevant documents for a natural-language query.
  For each document it prints the docid, a relevance score and a snippet (the first 512
  tokens of the document).
- `get_document <docid>` prints the text of one document (its first 8,192 tokens).

Examples:

    search "novelist born in Lagos who won a national prize in 1986"
    get_document 12345

Put the query in double quotes. If the query itself contains a double quote, a backtick or
a `$`, put it in single quotes instead.
