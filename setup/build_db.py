# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

"""
Chunk, embed, and store documents into a ChromaDB vector database.

Examples:

    olcf-user-docs:
        python build_db.py /workspace/olcf-user-docs "**/*.rst" /workspace/chroma_data \
            --file-type text --collection olcf_documentation \
            --chunk-size 1000 --chunk-overlap 200 --batch-size 200

    lumi-userguide:
        python build_db.py /workspace/lumi-userguide/docs "**/*.md" /workspace/chroma_data \
            --file-type markdown --collection LUMI_documentation \
            --chunk-size 1000 --chunk-overlap 200 --batch-size 200
"""

import argparse
from pathlib import Path

from langchain_community.document_loaders import (
    DirectoryLoader,
    TextLoader,
    UnstructuredMarkdownLoader,
    UnstructuredRSTLoader,
)
from langchain_text_splitters import RecursiveCharacterTextSplitter
import chromadb

LOADER_MAP = {
    "text": TextLoader,
    "markdown": UnstructuredMarkdownLoader,
    "rst": UnstructuredRSTLoader,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Chunk, embed, and store documents into ChromaDB.",
    )
    parser.add_argument("directory", type=str, help="Root directory containing the documents")
    parser.add_argument("pattern", type=str, help="Glob pattern for files to load (e.g. '**/*.rst')")
    parser.add_argument("save_path", type=str, help="Path for the persistent ChromaDB database")
    parser.add_argument(
        "--file-type",
        choices=list(LOADER_MAP.keys()),
        default="text",
        help="Loader type for the documents (default: text)",
    )
    parser.add_argument("--collection", type=str, default="documents", help="ChromaDB collection name (default: documents)")
    parser.add_argument("--chunk-size", type=int, default=1000, help="Text chunk size in characters (default: 1000)")
    parser.add_argument("--chunk-overlap", type=int, default=200, help="Overlap between chunks (default: 200)")
    parser.add_argument("--batch-size", type=int, default=200, help="Batch size for ChromaDB inserts (default: 200)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    loader_cls = LOADER_MAP[args.file_type]
    loader = DirectoryLoader(args.directory, glob=args.pattern, loader_cls=loader_cls)
    documents = loader.load()
    print(f"Loaded {len(documents)} documents from {args.directory}")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
    )
    chunks = splitter.split_documents(documents)
    print(f"Created {len(chunks)} chunks (size={args.chunk_size}, overlap={args.chunk_overlap})")

    chroma_client = chromadb.PersistentClient(path=args.save_path)
    collection = chroma_client.get_or_create_collection(name=args.collection)

    ids = [f"doc_{i}" for i in range(len(chunks))]
    texts = [chunk.page_content for chunk in chunks]
    metadatas = [chunk.metadata for chunk in chunks]

    for start in range(0, len(chunks), args.batch_size):
        end = min(start + args.batch_size, len(chunks))
        collection.add(
            documents=texts[start:end],
            ids=ids[start:end],
            metadatas=metadatas[start:end],
        )
        print(f"  Inserted {end}/{len(chunks)} chunks")

    print(f"Stored {collection.count()} chunks in ChromaDB at {args.save_path}")


if __name__ == "__main__":
    main()
