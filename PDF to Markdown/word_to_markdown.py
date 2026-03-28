from typing import Any, List
from llama_index.core.readers.base import BaseReader
from llama_index.core.schema import Document
import pypandoc
from pathlib import Path


class WordTextExtractor(BaseReader):
    @staticmethod
    def markdown_text_extraction(file: Path) -> str:
        """
        Extracts markdown text from a given Word file using pypandoc.

        :param file: Path to the Word document file.
        :return: Extracted markdown text.
        """
        try:
            return pypandoc.convert_file(str(file), to="markdown_strict", format="docx")
        except Exception as e:
            print(f"Error during markdown extraction: {e}")
            return ""

    def load_data(self, file: Path, **kwargs: Any) -> List[Document]:
        """
        Loads data from a Word document and converts it to markdown format.

        :param file: Path to the Word document file.
        :return: List of Document objects containing the extracted text.
        """
        try:
            markdown_text = self.markdown_text_extraction(file)
            if markdown_text:
                # Create a Document object with the extracted markdown text
                document = Document(text=markdown_text, **kwargs)
                return [document]
            else:
                return []
        except Exception as e:
            print(f"Error during data loading: {e}")
            return []