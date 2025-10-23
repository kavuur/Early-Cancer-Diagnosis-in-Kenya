#!/usr/bin/env python3
"""
Debug script to diagnose FAISS search issues
"""

import json
import numpy as np
import faiss
import pickle
from pathlib import Path
from medical_case_faiss import MedicalCaseFAISS


def debug_faiss_database():
    """Debug the FAISS database to identify why only first 2 cases are returned"""

    print("=== FAISS Database Debug ===\n")

    # Check if files exist
    json_file = 'cases_new.json'
    index_file = 'medical_cases.index'
    metadata_file = 'medical_cases_metadata.pkl'

    if not Path(json_file).exists():
        print(f"❌ JSON file not found: {json_file}")
        return

    # Load and inspect JSON data
    with open(json_file, 'r', encoding='utf-8') as f:
        cases_data = json.load(f)

    print(f"📄 JSON file contains {len(cases_data)} cases")

    # Show first few case IDs
    print("\nFirst 5 case IDs from JSON:")
    for i, case in enumerate(cases_data[:5]):
        case_id = case.get('case_id', f'case_{i + 1}')
        print(f"  {i + 1}. {case_id}")

    # Initialize FAISS system
    faiss_system = MedicalCaseFAISS()

    # Check if index files exist
    if Path(index_file).exists() and Path(metadata_file).exists():
        print(f"\n📁 Loading existing index files...")
        faiss_system.load_index(index_file, metadata_file)

        print(f"✅ Loaded index with {len(faiss_system.cases)} cases")
        print(f"✅ FAISS index ntotal: {faiss_system.index.ntotal}")

        # Verify case IDs match
        print("\nFirst 5 case IDs from loaded index:")
        for i, case in enumerate(faiss_system.cases[:5]):
            case_id = case.get('case_id', f'case_{i + 1}')
            print(f"  {i + 1}. {case_id}")

    else:
        print(f"\n🔄 Building new index...")
        faiss_system.build_database(json_file)
        print(f"✅ Built index with {len(faiss_system.cases)} cases")
        print(f"✅ FAISS index ntotal: {faiss_system.index.ntotal}")

    # Test search functionality
    print("\n=== Testing Search Functionality ===")

    test_queries = [
        "joint pain swelling",
        "breathing difficulty",
        "headache fever",
        "chest pain",
        "fatigue tiredness"
    ]

    for query in test_queries:
        print(f"\n🔍 Testing query: '{query}'")

        # Test with different similarity thresholds
        for threshold in [0.1, 0.2, 0.3]:
            results = faiss_system.search_similar_cases(query, k=5, similarity_threshold=threshold)
            print(f"  Threshold {threshold}: {len(results)} results")

            if results:
                print(f"    Best match: {results[0].case_id} (score: {results[0].similarity_score:.4f})")

                # Show all case IDs returned
                case_ids = [r.case_id for r in results]
                print(f"    All cases: {case_ids}")

    # Direct FAISS search test
    print(f"\n=== Direct FAISS Search Test ===")

    query = "joint pain swelling"
    query_embedding = faiss_system.model.encode([query])
    faiss.normalize_L2(query_embedding)

    # Search more cases
    search_k = min(len(faiss_system.cases), 20)
    similarities, indices = faiss_system.index.search(query_embedding.astype('float32'), search_k)

    print(f"Direct FAISS search for '{query}':")
    print(f"Search k: {search_k}")
    print(f"Results returned: {len(indices[0])}")

    print("\nAll results (top 20):")
    for i, (similarity, idx) in enumerate(zip(similarities[0], indices[0])):
        if idx < len(faiss_system.cases):
            case = faiss_system.cases[idx]
            case_id = case.get('case_id', f'case_{idx}')
            print(f"  {i + 1:2d}. Index: {idx:2d}, Case ID: {case_id}, Similarity: {similarity:.4f}")
        else:
            print(f"  {i + 1:2d}. Index: {idx} (INVALID)")

    # Check for potential issues
    print(f"\n=== Potential Issues Check ===")

    # Check if embeddings are properly normalized
    if hasattr(faiss_system, 'case_embeddings') and len(faiss_system.case_embeddings) > 0:
        norms = np.linalg.norm(faiss_system.case_embeddings, axis=1)
        print(f"Embedding norms (should be ~1.0): min={norms.min():.4f}, max={norms.max():.4f}")

    # Check case text extraction
    print(f"\nSample case text extractions:")
    for i in range(min(3, len(faiss_system.cases))):
        case = faiss_system.cases[i]
        text = faiss_system._extract_case_text(case)
        print(f"  Case {i + 1} ({case.get('case_id', f'case_{i + 1}')}):")
        print(f"    Text length: {len(text)} characters")
        print(f"    First 200 chars: {text[:200]}...")

    print(f"\n=== Summary ===")
    print(f"Total cases in JSON: {len(cases_data)}")
    print(f"Total cases in FAISS: {len(faiss_system.cases)}")
    print(f"FAISS index size: {faiss_system.index.ntotal}")

    if len(cases_data) == len(faiss_system.cases) == faiss_system.index.ntotal:
        print("✅ All counts match - data integrity looks good")
    else:
        print("❌ Counts don't match - potential data loss issue")


if __name__ == "__main__":
    debug_faiss_database()