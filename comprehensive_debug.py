#!/usr/bin/env python3
"""
Comprehensive debug script to diagnose and fix FAISS database issues
"""

import json
import numpy as np
import faiss
import pickle
import os
from pathlib import Path


def diagnose_faiss_issue():
    """Complete diagnosis of the FAISS database issue"""

    print("=== COMPREHENSIVE FAISS DIAGNOSIS ===\n")

    # Step 1: Check JSON file
    json_file = 'cases_new.json'
    if not Path(json_file).exists():
        print(f"❌ JSON file not found: {json_file}")
        return

    with open(json_file, 'r', encoding='utf-8') as f:
        cases_data = json.load(f)

    print(f"📄 JSON file contains {len(cases_data)} cases")

    # Show structure of first case
    if cases_data:
        print("\n📋 First case structure:")
        first_case = cases_data[0]
        for key, value in first_case.items():
            if isinstance(value, dict):
                print(f"  {key}: {type(value).__name__} with keys: {list(value.keys())}")
            elif isinstance(value, list):
                print(f"  {key}: {type(value).__name__} with {len(value)} items")
            else:
                print(f"  {key}: {type(value).__name__}")

    # Step 2: Check existing index files
    index_file = 'medical_cases.index'
    metadata_file = 'medical_cases_metadata.pkl'

    if Path(index_file).exists() and Path(metadata_file).exists():
        print(f"\n📁 Existing index files found")

        # Load index
        try:
            index = faiss.read_index(index_file)
            print(f"✅ FAISS index loaded: {index.ntotal} vectors")

            with open(metadata_file, 'rb') as f:
                metadata = pickle.load(f)

            cases_in_index = len(metadata['cases'])
            print(f"✅ Metadata loaded: {cases_in_index} cases")

            if cases_in_index != len(cases_data):
                print(f"❌ MISMATCH: JSON has {len(cases_data)} cases but index has {cases_in_index}")
                print("   This is likely the root cause of your issue!")

        except Exception as e:
            print(f"❌ Error loading existing index: {e}")

    # Step 3: Rebuild index properly
    print(f"\n🔄 Rebuilding index from scratch...")

    from sentence_transformers import SentenceTransformer

    # Initialize model
    model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')

    # Process all cases
    processed_cases = []
    case_texts = []

    for i, case in enumerate(cases_data):
        # Add case_id if missing
        if 'case_id' not in case:
            case['case_id'] = f"case_{i + 1}"

        # Extract text
        text = extract_case_text(case)

        if text.strip():  # Only include cases with non-empty text
            processed_cases.append(case)
            case_texts.append(text)
            print(f"✅ Processed case {i + 1}: {case['case_id']} ({len(text)} chars)")
        else:
            print(f"⚠️  Skipped case {i + 1}: {case.get('case_id', 'no-id')} (empty text)")

    print(f"\n📊 Summary:")
    print(f"  Total cases in JSON: {len(cases_data)}")
    print(f"  Cases with valid text: {len(processed_cases)}")
    print(f"  Cases to be indexed: {len(case_texts)}")

    if len(case_texts) < len(cases_data):
        print(f"⚠️  {len(cases_data) - len(case_texts)} cases were skipped due to empty text")

    # Create embeddings
    print(f"\n🔄 Creating embeddings for {len(case_texts)} cases...")
    embeddings = model.encode(case_texts, show_progress_bar=True)
    print(f"✅ Created embeddings: {embeddings.shape}")

    # Build FAISS index
    dimension = embeddings.shape[1]
    index = faiss.IndexFlatIP(dimension)

    # Normalize embeddings
    faiss.normalize_L2(embeddings)

    # Add to index
    index.add(embeddings.astype('float32'))

    print(f"✅ Built FAISS index with {index.ntotal} vectors")

    # Save new index
    faiss.write_index(index, index_file)

    metadata = {
        'cases': processed_cases,
        'case_embeddings': embeddings,
        'dimension': dimension
    }

    with open(metadata_file, 'wb') as f:
        pickle.dump(metadata, f)

    print(f"✅ Saved new index and metadata")

    # Test the new index
    print(f"\n🧪 Testing new index...")

    test_queries = [
        "blood in urine",
        "breathing difficulty night cough",
        "joint pain swelling",
        "headache fever"
    ]

    for query in test_queries:
        print(f"\n🔍 Testing: '{query}'")

        # Create query embedding
        query_embedding = model.encode([query])
        faiss.normalize_L2(query_embedding)

        # Search
        k = min(10, index.ntotal)
        similarities, indices = index.search(query_embedding.astype('float32'), k)

        print(f"  Results: {len(indices[0])} matches")
        print(f"  Top 5 similarities: {similarities[0][:5]}")
        print(f"  Top 5 indices: {indices[0][:5]}")

        # Show case details
        for i, (sim, idx) in enumerate(zip(similarities[0][:5], indices[0][:5])):
            if idx < len(processed_cases):
                case = processed_cases[idx]
                case_id = case.get('case_id', f'case_{idx}')
                print(f"    {i + 1}. {case_id} (similarity: {sim:.4f})")

    print(f"\n✅ Diagnosis complete!")
    print(f"   New index created with {index.ntotal} cases")
    print(f"   Restart your Flask app to use the new index")


def extract_case_text(case):
    """Extract text from case for embedding"""
    text_parts = []

    # Patient background
    if 'patient_background' in case:
        bg = case['patient_background']
        if isinstance(bg, dict):
            if 'english' in bg and bg['english']:
                text_parts.append(f"Background: {bg['english']}")
            if 'swahili' in bg and bg['swahili']:
                text_parts.append(f"Background (Swahili): {bg['swahili']}")
        elif isinstance(bg, str) and bg:
            text_parts.append(f"Background: {bg}")

    # Chief complaint
    if 'chief_complaint_history' in case:
        cc = case['chief_complaint_history']
        if isinstance(cc, dict):
            if 'english' in cc and cc['english']:
                text_parts.append(f"Chief Complaint: {cc['english']}")
            if 'swahili' in cc and cc['swahili']:
                text_parts.append(f"Chief Complaint (Swahili): {cc['swahili']}")
        elif isinstance(cc, str) and cc:
            text_parts.append(f"Chief Complaint: {cc}")

    # Medical history
    if 'medical_social_history' in case:
        mh = case['medical_social_history']
        if isinstance(mh, dict):
            if 'english' in mh and mh['english']:
                text_parts.append(f"Medical History: {mh['english']}")
            if 'swahili' in mh and mh['swahili']:
                text_parts.append(f"Medical History (Swahili): {mh['swahili']}")
        elif isinstance(mh, str) and mh:
            text_parts.append(f"Medical History: {mh}")

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

    # Recommended questions
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
            text_parts.append(f"Questions: {' '.join(questions)}")

    # Red flags
    if 'red_flags' in case and isinstance(case['red_flags'], dict):
        red_flags = []
        for key, value in case['red_flags'].items():
            if value and str(value).strip():
                red_flags.append(f"{key}: {value}")
        if red_flags:
            text_parts.append(f"Red Flags: {' '.join(red_flags)}")

    if 'Suspected_illness' in case and isinstance(case['Suspected_illness'], dict):
        red_flags = []
        for key, value in case['Suspected_illness'].items():
            if value and str(value).strip():
                red_flags.append(f"{key}: {value}")
        if red_flags:
            text_parts.append(f"Suspected_illness: {' '.join(red_flags)}")

    result = " ".join(text_parts)
    return result

#Suspected_illness
if __name__ == "__main__":
    diagnose_faiss_issue()