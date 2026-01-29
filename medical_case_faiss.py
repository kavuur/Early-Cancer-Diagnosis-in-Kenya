import json
import numpy as np
import faiss
import pickle
from typing import List, Dict, Any, Tuple
from sentence_transformers import SentenceTransformer
import logging
from dataclasses import dataclass
from pathlib import Path

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class CaseSearchResult:
    """Data class for search results"""
    case_id: str
    similarity_score: float
    patient_background: Dict[str, str]
    chief_complaint: Dict[str, str]
    medical_history: Dict[str, str]
    opening_statement: Dict[str, str]
    recommended_questions: List[Dict]
    red_flags: Dict[str, Any]
    Suspected_illness: str


class MedicalCaseFAISS:
    """
    FAISS-based medical case database for similarity search and question recommendation
    Optimized for Flask web application use
    """

    def __init__(self, model_name: str = 'sentence-transformers/all-MiniLM-L6-v2'):
        """
        Initialize the FAISS database system

        Args:
            model_name: Name of the sentence transformer model to use for embeddings
        """
        self.model = SentenceTransformer(model_name)
        self.index = None
        self.cases = []
        self.case_embeddings = []
        self.dimension = None
        logger.info(f"Initialized MedicalCaseFAISS with model: {model_name}")

    def _extract_case_text(self, case: Dict[str, Any]) -> str:
        """
        Extract and combine all relevant text from a case for embedding

        Args:
            case: Dictionary containing case information

        Returns:
            Combined text string for embedding
        """
        text_parts = []

        # Patient background (both languages)
        if 'patient_background' in case:
            bg = case['patient_background']
            if isinstance(bg, dict):
                if 'english' in bg and bg['english']:
                    text_parts.append(f"Background: {bg['english']}")
                if 'swahili' in bg and bg['swahili']:
                    text_parts.append(f"Background (Swahili): {bg['swahili']}")
            elif isinstance(bg, str) and bg:
                text_parts.append(f"Background: {bg}")

        # Chief complaint history
        if 'chief_complaint_history' in case:
            cc = case['chief_complaint_history']
            if isinstance(cc, dict):
                if 'english' in cc and cc['english']:
                    text_parts.append(f"Chief Complaint: {cc['english']}")
                if 'swahili' in cc and cc['swahili']:
                    text_parts.append(f"Chief Complaint (Swahili): {cc['swahili']}")
            elif isinstance(cc, str) and cc:
                text_parts.append(f"Chief Complaint: {cc}")

        # Medical social history
        if 'medical_social_history' in case:
            msh = case['medical_social_history']
            if isinstance(msh, dict):
                if 'english' in msh and msh['english']:
                    text_parts.append(f"Medical History: {msh['english']}")
                if 'swahili' in msh and msh['swahili']:
                    text_parts.append(f"Medical History (Swahili): {msh['swahili']}")
            elif isinstance(msh, str) and msh:
                text_parts.append(f"Medical History: {msh}")

        # Opening statement
        if 'opening_statement' in case:
            os = case['opening_statement']
            if isinstance(os, dict):
                if 'english' in os and os['english']:
                    text_parts.append(f"Opening Statement: {os['english']}")
                if 'swahili' in os and os['swahili']:
                    text_parts.append(f"Opening Statement (Swahili): {os['swahili']}")
            elif isinstance(os, str) and os:
                text_parts.append(f"Opening Statement: {os}")

        # Recommended questions (extract key symptoms/conditions)
        if 'recommended_questions' in case and isinstance(case['recommended_questions'], list):
            questions = []
            for q in case['recommended_questions']:
                if isinstance(q, dict):
                    if 'question' in q and isinstance(q['question'], dict):
                        if 'english' in q['question'] and q['question']['english']:
                            questions.append(q['question']['english'])
                    if 'response' in q and isinstance(q['response'], dict):
                        if 'english' in q['response'] and q['response']['english']:
                            questions.append(q['response']['english'])
            if questions:
                text_parts.append(f"Questions and Responses: {' '.join(questions)}")

        # Red flags
        if 'red_flags' in case and isinstance(case['red_flags'], dict):
            red_flags_text = []
            for key, value in case['red_flags'].items():
                if value and str(value).strip():  # Only include non-empty values
                    red_flags_text.append(f"{key}: {value}")
            if red_flags_text:
                text_parts.append(f"Red Flags: {' '.join(red_flags_text)}")
        if 'Suspected_illness' in case and isinstance(case['Suspected_illness'], dict):
            Suspected_illness = []
            for key, value in case['Suspected_illness'].items():
                if value and str(value).strip():  # Only include non-empty values
                    Suspected_illness.append(f"{key}: {value}")
            if Suspected_illness:
                text_parts.append(f"Suspected_illness: {' '.join(red_flags_text)}")

        return " ".join(text_parts)
#Suspected_illness
    def build_database(self, json_file_path: str) -> None:
        """
        Build FAISS database from JSON file

        Args:
            json_file_path: Path to the JSON file containing cases
        """
        logger.info(f"Loading cases from {json_file_path}")

        # Load JSON data
        with open(json_file_path, 'r', encoding='utf-8') as f:
            cases_data = json.load(f)

        logger.info(f"Loaded {len(cases_data)} cases from JSON")

        # Process cases and filter out empty ones
        processed_cases = []
        case_texts = []

        for i, case in enumerate(cases_data):
            # Add case_id if not present
            if 'case_id' not in case:
                case['case_id'] = f"case_{i + 1}"

            # Extract text
            text = self._extract_case_text(case)

            if text.strip():  # Only include cases with non-empty text
                processed_cases.append(case)
                case_texts.append(text)
                logger.info(f"Processed case {i + 1}: {case['case_id']} ({len(text)} chars)")
            else:
                logger.warning(f"Skipped case {i + 1}: {case.get('case_id', 'no-id')} (empty text)")

        self.cases = processed_cases
        logger.info(f"Processing {len(self.cases)} cases with valid text content")

        if not case_texts:
            raise ValueError("No valid cases found with text content")

        logger.info("Creating embeddings...")
        embeddings = self.model.encode(case_texts, show_progress_bar=True)
        self.case_embeddings = embeddings

        # Initialize FAISS index
        self.dimension = embeddings.shape[1]
        self.index = faiss.IndexFlatIP(self.dimension)  # Inner product for similarity

        # Normalize embeddings for cosine similarity
        faiss.normalize_L2(embeddings)

        # Add embeddings to index
        self.index.add(embeddings.astype('float32'))

        logger.info(f"Built FAISS index with {self.index.ntotal} cases")

    def search_similar_cases(self, query: str, k: int = 5, similarity_threshold: float = 0.5) -> List[CaseSearchResult]:
        """
        Search for similar cases based on query

        Args:
            query: User's symptom description or partial history
            k: Number of similar cases to return
            similarity_threshold: Minimum similarity score to include in results

        Returns:
            List of similar cases with similarity scores >= threshold
        """
        if self.index is None:
            raise ValueError("Database not built. Call build_database() first.")

        # Create embedding for query
        query_embedding = self.model.encode([query])
        faiss.normalize_L2(query_embedding)

        # Search ALL cases to find best matches
        search_k = min(len(self.cases), 50)  # Search more cases to find best matches
        similarities, indices = self.index.search(query_embedding.astype('float32'), search_k)

        # Debug logging
        logger.info(f"Query: {query}")
        logger.info(f"Search returned {len(indices[0])} results")
        logger.info(f"Top 5 similarities: {similarities[0][:5]}")
        logger.info(f"Top 5 indices: {indices[0][:5]}")

        # Prepare results and filter by similarity threshold
        results = []
        for i, (similarity, idx) in enumerate(zip(similarities[0], indices[0])):
            # Check if index is valid and similarity meets threshold
            if idx >= 0 and idx < len(self.cases) and similarity >= similarity_threshold:
                case = self.cases[idx]

                # Additional debug info
                logger.info(f"Result {i}: case_id={case.get('case_id', f'case_{idx}')}, "
                            f"similarity={similarity:.4f}, index={idx}")

                result = CaseSearchResult(
                    case_id=case.get('case_id', f'case_{idx}'),
                    similarity_score=float(similarity),
                    patient_background=case.get('patient_background', {}),
                    chief_complaint=case.get('chief_complaint_history', {}),
                    medical_history=case.get('medical_social_history', {}),
                    opening_statement=case.get('opening_statement', {}),
                    recommended_questions=case.get('recommended_questions', []),
                    red_flags=case.get('red_flags', {}),
                    Suspected_illness=case.get('Suspected_illness', {})
                )
                results.append(result)

                # Stop if we have enough results
                if len(results) >= k:
                    break

        logger.info(f"Returning {len(results)} results after filtering")
        return results

    # def suggest_questions(self, query: str, k: int = 3, max_questions: int = 10, similarity_threshold: float = 0.5) -> \
    # List[Dict]:
    #     """
    #     Suggest questions based on similar cases
    #
    #     Args:
    #         query: User's symptom description or partial history
    #         k: Number of similar cases to consider
    #         max_questions: Maximum number of questions to return
    #         similarity_threshold: Minimum similarity score to include in results
    #
    #     Returns:
    #         List of suggested questions with both English and Swahili versions
    #     """
    #     similar_cases = self.search_similar_cases(query, k, similarity_threshold)
    #
    #     # Collect all questions from similar cases
    #     all_questions = []
    #     question_set = set()  # To avoid duplicates
    #
    #     for case_result in similar_cases:
    #         for q in case_result.recommended_questions:
    #             if 'question' in q:
    #                 question_text = q['question'].get('english', '')
    #                 if question_text and question_text not in question_set:
    #                     question_set.add(question_text)
    #                     all_questions.append({
    #                         'question': q['question'],
    #                         'response': q.get('response', {}),
    #                         'similarity_score': case_result.similarity_score,
    #                         'case_id': case_result.case_id
    #                     })
    #
    #     # Sort by similarity score and return top questions
    #     all_questions.sort(key=lambda x: x['similarity_score'], reverse=True)
    #     return all_questions[:max_questions]
    def suggest_questions(
            self,
            query: str,
            k: int = 5,
            max_questions: int = 10,
            similarity_threshold: float = 0.45
    ) -> List[Dict]:
        """
        Suggest questions from top similar cases whose similarity exceeds threshold,
        compiling both English and Swahili versions.

        Args:
            query: User's symptom description
            k: Number of top similar cases to consider
            max_questions: Max number of suggestions to return
            similarity_threshold: Only include cases with similarity >= this value

        Returns:
            List of suggested questions with English and Swahili text.
        """
        logger.info(
            f"Generating suggested questions for query: '{query}' with similarity threshold {similarity_threshold}")

        # Get top similar cases above threshold
        similar_cases = self.search_similar_cases(query, k=k, similarity_threshold=similarity_threshold)

        logger.info(f"Found {len(similar_cases)} cases for suggestion extraction")

        # Collect questions from those cases
        all_questions = []
        seen_questions = set()

        for case_result in similar_cases:
            for q in case_result.recommended_questions:
                if 'question' in q and isinstance(q['question'], dict):
                    eng = q['question'].get('english', '').strip()
                    swa = q['question'].get('swahili', '').strip()

                    # Create a unique key to avoid duplicates
                    key = (eng.lower(), swa.lower())
                    if key in seen_questions:
                        continue

                    seen_questions.add(key)

                    all_questions.append({
                        'question': {
                            'english': eng,
                            'swahili': swa
                        },
                        'case_id': case_result.case_id,
                        'similarity_score': case_result.similarity_score
                    })

        # Sort by similarity descending
        all_questions.sort(key=lambda x: x['similarity_score'], reverse=True)

        # Limit to top N
        suggestions = all_questions[:max_questions]

        logger.info(f"Returning {len(suggestions)} suggested questions")
        return suggestions

    def save_index(self, index_path: str, metadata_path: str) -> None:
        """
        Save FAISS index and metadata to disk

        Args:
            index_path: Path to save FAISS index
            metadata_path: Path to save metadata (cases and embeddings)
        """
        if self.index is None:
            raise ValueError("No index to save. Build database first.")

        # Save FAISS index
        faiss.write_index(self.index, index_path)

        # Save metadata
        metadata = {
            'cases': self.cases,
            'case_embeddings': self.case_embeddings,
            'dimension': self.dimension
        }
        with open(metadata_path, 'wb') as f:
            pickle.dump(metadata, f)

        logger.info(f"Saved index to {index_path} and metadata to {metadata_path}")

    def load_index(self, index_path: str, metadata_path: str) -> None:
        """
        Load FAISS index and metadata from disk

        Args:
            index_path: Path to FAISS index file
            metadata_path: Path to metadata file
        """
        # Load FAISS index
        self.index = faiss.read_index(index_path)

        # Load metadata
        with open(metadata_path, 'rb') as f:
            metadata = pickle.load(f)

        self.cases = metadata['cases']
        self.case_embeddings = metadata['case_embeddings']
        self.dimension = metadata['dimension']

        logger.info(f"Loaded index from {index_path} and metadata from {metadata_path}")
        logger.info(f"Database contains {len(self.cases)} cases")

    def get_case_details(self, case_id: str) -> Dict[str, Any]:
        """
        Get detailed information about a specific case

        Args:
            case_id: ID of the case to retrieve

        Returns:
            Complete case information or None if not found
        """
        for case in self.cases:
            if case.get('case_id') == case_id:
                return case
        return None

    def get_stats(self) -> Dict[str, Any]:
        """
        Get database statistics

        Returns:
            Dictionary with database statistics
        """
        if not self.cases:
            return {'total_cases': 0, 'index_built': False}

        return {
            'total_cases': len(self.cases),
            'index_built': self.index is not None,
            'dimension': self.dimension,
            'model_name': self.model.get_sentence_embedding_dimension() if hasattr(self.model,
                                                                                   'get_sentence_embedding_dimension') else 'Unknown'
        }

    def debug_search(self, query: str, k: int = 10) -> None:
        """
        Debug function to analyze search results across all cases
        """
        if self.index is None:
            raise ValueError("Database not built. Call build_database() first.")

        # Create embedding for query
        query_embedding = self.model.encode([query])
        faiss.normalize_L2(query_embedding)

        # Search ALL cases
        search_k = min(len(self.cases), 50)
        similarities, indices = self.index.search(query_embedding.astype('float32'), search_k)

        print(f"\nDEBUG: Query '{query}'")
        print(f"Total cases in database: {len(self.cases)}")
        print(f"FAISS index total: {self.index.ntotal}")
        print(f"Search k: {search_k}")
        print(f"Results returned: {len(indices[0])}")

        print("\nTop 10 results:")
        for i, (similarity, idx) in enumerate(zip(similarities[0][:10], indices[0][:10])):
            if idx < len(self.cases):
                case = self.cases[idx]
                case_id = case.get('case_id', f'case_{idx}')
                print(f"{i + 1}. Index: {idx}, Case ID: {case_id}, Similarity: {similarity:.4f}")
                # Show some case content for verification
                bg = case.get('patient_background', {}).get('english', '')[:100]
                print(f"   Background: {bg}...")
            else:
                print(f"{i + 1}. Index: {idx} (INVALID - exceeds case count)")


# For standalone usage
if __name__ == "__main__":
    # This allows the original functionality to still work
    from pathlib import Path
    import sys


    def main():
        """
        Main function to build the database
        """
        faiss_system = MedicalCaseFAISS()

        json_file = 'cases_new.json'

        if not Path(json_file).exists():
            print(f"Error: {json_file} not found!")
            print("Please ensure the JSON file is in the same directory.")
            sys.exit(1)

        try:
            print("Building FAISS database...")
            faiss_system.build_database(json_file)

            # Save the index
            faiss_system.save_index('medical_cases.index', 'medical_cases_metadata.pkl')

            print("Database built successfully!")
            print("Files created:")
            print("- medical_cases.index")
            print("- medical_cases_metadata.pkl")

            # Debug: Test the search functionality
            print("\nTesting search functionality:")
            faiss_system.debug_search("pain joint swelling", k=10)

            print("\nYou can now run the Flask application.")

        except Exception as e:
            print(f"Error building database: {e}")
            sys.exit(1)


    main()