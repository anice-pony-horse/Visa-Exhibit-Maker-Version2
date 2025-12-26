"""
Visa Exhibit Generator V2.0
===========================

Professional exhibit package generator for visa petitions.
Features 6-stage workflow with AI classification.0

Stages:
1. Context (optional) - Case information
2. Upload - PDFs, URLs, Google Drive
3. Classify - AI auto-categorization
4. Review - Manual reorder + text commands
5. Generate - Background processing
6. Complete - Download, email, share link

EXHIBIT ORGANIZATION REFERENCE:
../VISA_EXHIBIT_RAG_COMPREHENSIVE_INSTRUCTIONS.md
"""

import streamlit as st
import streamlit.components.v1 as components
import os
import io
import tempfile
from streamlit.components.v1 import html
from pathlib import Path
from typing import List, Dict, Optional, Any
import zipfile
from datetime import datetime
import shutil
import hashlib

# Import our modules
from pdf_handler import PDFHandler
from exhibit_processor import ExhibitProcessor
from google_drive import GoogleDriveHandler
from archive_handler import ArchiveHandler

# Import V2 components
from components.stage_navigator import StageNavigator, STAGES, render_stage_header
from components.intake_form import render_intake_form, get_case_context, render_context_summary
from components.url_manager import render_url_manager, get_url_list, URLManager
from components.ai_classifier import (
    AIClassifier, ClassificationResult,
    render_classification_ui, get_classifications, save_classifications
)
from components.exhibit_editor import (
    render_exhibit_editor, get_exhibits, set_exhibits_from_classifications
)
from components.background_processor import (
    BackgroundProcessor, render_processing_ui, get_processor
)
from components.thumbnail_grid import render_exhibit_preview
from components.email_sender import render_email_form
from components.link_generator import render_link_generator

# Import template engine for cover letters
from templates.docx_engine import DOCXTemplateEngine
from components.thumbnail_grid import generate_thumbnail

# Check if compression is available
try:
    from compress_handler import USCISPDFCompressor, compress_pdf_batch
    COMPRESSION_AVAILABLE = True
except ImportError:
    COMPRESSION_AVAILABLE = False


# Page config
st.set_page_config(
    page_title="Visa Exhibit Generator V2",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        color: #1f77b4;
        text-align: center;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        font-size: 1.2rem;
        color: #666;
        text-align: center;
        margin-bottom: 1rem;
    }
    .version-badge {
        background: #28a745;
        color: white;
        padding: 0.25rem 0.5rem;
        border-radius: 0.25rem;
        font-size: 0.8rem;
        display: inline-block;
    }
    .feature-box {
        padding: 1.5rem;
        border-radius: 0.5rem;
        background-color: #f0f2f6;
        margin: 1rem 0;
    }
    .success-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: #d4edda;
        border: 1px solid #c3e6cb;
        color: #155724;
    }
    .info-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: #d1ecf1;
        border: 1px solid #bee5eb;
        color: #0c5460;
    }
    .warning-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: #fff3cd;
        border: 1px solid #ffeaa7;
        color: #856404;
    }
    .stat-card {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: white;
        border: 1px solid #ddd;
        text-align: center;
    }
    .stat-value {
        font-size: 2rem;
        font-weight: bold;
        color: #1f77b4;
    }
    .stat-label {
        font-size: 0.9rem;
        color: #666;
    }
    .stage-container {
        padding: 1.5rem;
        background: #fafafa;
        border-radius: 0.5rem;
        min-height: 400px;
    }
</style>
""", unsafe_allow_html=True)

def init_session_state():
    """Initialize all session state variables"""
    defaults = {
        'exhibits_generated': False,
        'compression_stats': None,
        'exhibit_list': [],
        'uploaded_files': [],
        'file_paths': [],
        'output_file': None,
        'processing_complete': False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

def delete_file(idx):
    uploaded_files = st.session_state.get('uploaded_files', [])
    uploaded_meta = st.session_state.get('uploaded_meta', [])
    # Validate index
    if not isinstance(idx, int):
        return
    if 0 <= idx < len(uploaded_files):
        # Remove file
        try:
            uploaded_files.pop(idx)
        except Exception:
            pass
        # Remove meta if present
        if 0 <= idx < len(uploaded_meta):
            try:
                uploaded_meta.pop(idx)
            except Exception:
                pass

        # Save back
        st.session_state.uploaded_files = uploaded_files
        st.session_state.uploaded_meta = uploaded_meta

        # Adjust preview index if necessary (handle None)
        preview_idx = st.session_state.get('preview_file_index')
        if preview_idx is None:
            # nothing to do
            pass
        else:
            try:
                if preview_idx == idx:
                    st.session_state.preview_file_index = None
                elif isinstance(preview_idx, int) and preview_idx > idx:
                    st.session_state.preview_file_index = preview_idx - 1
            except Exception:
                st.session_state.preview_file_index = None

        # Adjust selected index if present
        sel = st.session_state.get('selected_upload_index')
        if isinstance(sel, int):
            if sel == idx:
                st.session_state.selected_upload_index = None
            elif sel > idx:
                st.session_state.selected_upload_index = max(0, sel - 1)

        st.rerun()

def rotate_file(idx):
    if 0 <= idx < len(st.session_state.uploaded_meta):
        meta = st.session_state.uploaded_meta[idx]
        current_rotation = meta.get('rotation', 0)
        meta['rotation'] = (current_rotation + 90) % 360
        # Try to regenerate thumbnail for this file to reflect rotation
        try:
            uploaded_files = st.session_state.get('uploaded_files', [])
            if 0 <= idx < len(uploaded_files):
                f = uploaded_files[idx]
                # Read bytes
                f.seek(0)
                content = f.read()
                f.seek(0)
                try:
                    # Use existing generate_thumbnail helper to update this file's thumbnail
                    rot = int(meta.get('rotation', 0) or 0)
                    thumb_size = (180, 240) if rot % 180 == 0 else (240, 180)
                    new_thumb = generate_thumbnail(pdf_bytes=content, page=0, size=thumb_size, rotation=rot)
                    if new_thumb:
                        meta['thumb'] = new_thumb
                        st.session_state.uploaded_meta = st.session_state.get('uploaded_meta', [])
                        st.rerun()
                except Exception:
                    # Ignore thumbnail/render errors for this single file
                    pass
        except Exception:
            pass

def duplicate_file(idx):
    """Duplicate an uploaded file in-session and insert the copy after the original."""
    try:
        uploaded = st.session_state.get('uploaded_files', []) or []
        meta = st.session_state.get('uploaded_meta', []) or []

        if not isinstance(idx, int):
            return
        if not (0 <= idx < len(uploaded)):
            return

        src = uploaded[idx]
        # Read bytes to make a stable copy
        content = None
        try:
            src.seek(0)
            content = src.read()
            src.seek(0)
        except Exception:
            content = None

        if content is not None:
            buf = io.BytesIO(content)
            buf.name = getattr(src, 'name', f'copy_{idx}')
            try:
                buf.size = len(content)
            except Exception:
                pass
        else:
            # Fallback to shallow copy
            buf = src

        # Duplicate metadata if present
        if 0 <= idx < len(meta):
            new_meta = dict(meta[idx])
            orig_name = new_meta.get('name') or getattr(src, 'name', None) or f'Document {idx+1}'
            new_meta['name'] = f"{orig_name}"
            new_meta['rotation'] = new_meta.get('rotation', 0)
        else:
            new_meta = {'name': getattr(buf, 'name', f'Document {idx+1}'), 'rotation': 0, 'pages': '', 'thumb': None}

        # Attempt to generate a thumbnail for the copy
        try:
            rot_nm = int(new_meta.get('rotation', 0) or 0)
            size_nm = (180, 240) if rot_nm % 180 == 0 else (240, 180)
            if content is not None:
                thumb = generate_thumbnail(pdf_bytes=content, page=0, size=size_nm, rotation=rot_nm)
                if thumb:
                    new_meta['thumb'] = thumb
        except Exception:
            pass

        insert_at = idx + 1
        uploaded.insert(insert_at, buf)
        meta.insert(insert_at, new_meta)

        st.session_state.uploaded_files = uploaded
        st.session_state.uploaded_meta = meta

        # Focus the duplicated item
        st.session_state.selected_upload_index = insert_at
        st.session_state.preview_file_index = insert_at

        # Clear dynamic keys to avoid widget collisions
        dynamic_prefixes = (
            'preview_', 'move_mode_', 'view_card_', 'dup_card_', 'del_card_',
            'insert_here_', 'insert_files_', 'list_del_'
        )
        keys_to_clear = [k for k in list(st.session_state.keys()) if any(k.startswith(p) for p in dynamic_prefixes)]
        for k in keys_to_clear:
            try:
                st.session_state.pop(k, None)
            except Exception:
                pass

        st.rerun()
    except Exception:
        return


def process_bridge_command():
    """Callback to handle bridge commands immediately"""
    if st.session_state.get("action_command"):
        cmd = st.session_state.action_command
        # Clear immediately
        st.session_state.action_command = ""
        
        parts = cmd.split(":")
        if len(parts) == 2:
            action, idx_str = parts
            try:
                idx = int(idx_str)
                if action == "delete":
                    delete_file(idx)
                elif action == "rotate":
                    rotate_file(idx)
                elif action == "duplicate":
                    duplicate_file(idx)
                elif action == "preview":
                    if st.session_state.get('preview_file_index') == idx:
                        st.session_state.preview_file_index = None
                    else:
                        st.session_state.preview_file_index = idx
                    # Force rerun if not already triggered by file ops
                    st.rerun()
            except ValueError:
                pass


def render_sidebar():
    """Render sidebar configuration"""
    with st.sidebar:
        st.header("⚙️ Configuration")

        # Visa type selection
        visa_type = st.selectbox(
            "Visa Type",
            ["O-1A", "O-1B", "O-2", "P-1A", "P-1B", "P-1S", "EB-1A", "EB-1B", "EB-2 NIW"],
            help="Select the visa category for your petition"
        )

        # Exhibit numbering style
        numbering_style = st.selectbox(
            "Exhibit Numbering",
            ["Letters (A, B, C...)", "Numbers (1, 2, 3...)", "Roman (I, II, III...)"],
            help="How to number your exhibits"
        )

        # Convert numbering style to code
        numbering_map = {
            "Letters (A, B, C...)": "letters",
            "Numbers (1, 2, 3...)": "numbers",
            "Roman (I, II, III...)": "roman"
        }
        numbering_code = numbering_map[numbering_style]

        st.divider()

        # Compression settings
        st.header("🗜️ PDF Compression")

        if not COMPRESSION_AVAILABLE:
            st.warning("⚠️ Compression not available. Install PyMuPDF.")
            enable_compression = False
            quality_code = "high"
            smallpdf_key = None
        else:
            enable_compression = st.checkbox(
                "Enable PDF Compression",
                value=True,
                help="Compress PDFs to reduce file size (50-75% reduction)"
            )

            if enable_compression:
                quality_preset = st.selectbox(
                    "Compression Quality",
                    ["High Quality (USCIS Recommended)", "Balanced", "Maximum Compression"]
                )
                quality_map = {
                    "High Quality (USCIS Recommended)": "high",
                    "Balanced": "balanced",
                    "Maximum Compression": "maximum"
                }
                quality_code = quality_map[quality_preset]

                with st.expander("🔑 SmallPDF API Key (Optional)"):
                    smallpdf_key = st.text_input("SmallPDF API Key", type="password")
            else:
                quality_code = "high"
                smallpdf_key = None

        st.divider()

        # AI Classification settings
        st.header("🤖 AI Classification")

        enable_ai = st.checkbox(
            "Enable AI Classification",
            value=True,
            help="Use Claude API to auto-classify documents"
        )

        if enable_ai:
            with st.expander("🔑 Anthropic API Key"):
                anthropic_key = st.text_input(
                    "API Key",
                    type="password",
                    help="Get key at console.anthropic.com"
                )
                if anthropic_key:
                    st.session_state['anthropic_api_key'] = anthropic_key
                    st.success("✓ API key set")
                else:
                    st.info("Using rule-based classification")
        else:
            st.session_state['anthropic_api_key'] = None

        st.divider()

        # Output options
        st.header("📋 Options")

        add_toc = st.checkbox("Generate Table of Contents", value=True)
        add_archive = st.checkbox("Archive URLs (archive.org)", value=False)
        merge_pdfs = st.checkbox("Merge into single PDF", value=True)
        add_cover_letter = st.checkbox("Generate Cover Letter", value=True)
        add_filing_instructions = st.checkbox("Generate Filing Instructions (DIY)", value=False)
        include_full_text_images = st.checkbox("Include full extracted text & images in package", value=False,
                              help="Append a readable transcription and extracted images for each exhibit")

        st.divider()

        # Documentation
        with st.expander("📚 Help"):
            st.markdown("""
            **6-Stage Workflow:**
            1. **Context** - Optional case info
            2. **Upload** - Add documents
            3. **Classify** - AI categorization
            4. **Review** - Reorder exhibits
            5. **Generate** - Create package
            6. **Complete** - Download & share

            **Supported Visa Types:**
            O-1A, O-1B, O-2, P-1A, P-1B, P-1S, EB-1A, EB-1B, EB-2 NIW
            """)

    return {
        'visa_type': visa_type,
        'numbering_style': numbering_code,
        'enable_compression': enable_compression,
        'quality_preset': quality_code,
        'smallpdf_api_key': smallpdf_key if enable_compression else None,
        'enable_ai': enable_ai,
        'add_toc': add_toc,
        'add_archive': add_archive,
        'merge_pdfs': merge_pdfs,
        'add_cover_letter': add_cover_letter,
            'add_filing_instructions': add_filing_instructions,
            'include_full_text_images': include_full_text_images,
    }


def render_stage_1_context(navigator: StageNavigator):
    """Stage 1: Optional Context Form"""
    st.markdown('<div class="stage-container">', unsafe_allow_html=True)

    context = render_intake_form()

    st.markdown('</div>', unsafe_allow_html=True)

    # Navigation
    def on_next():
        # Context is saved automatically
        pass

    navigator.render_navigation_buttons(
        on_next=on_next,
        next_label="Continue to Upload"
    )


def render_stage_2_upload(navigator: StageNavigator, config: Dict):
    """Stage 2: Document Upload"""
    st.markdown('<div class="stage-container">', unsafe_allow_html=True)

    # Show context summary if provided
    render_context_summary()

    # Upload tabs
    tab1, tab2, tab3 = st.tabs(["📁 Upload Files", "📎 URL Documents", "☁️ Google Drive"])

    with tab1:
        st.subheader("Upload PDF Files")

        upload_method = st.radio(
            "Upload Method",
            ["Individual PDFs", "ZIP Archive"],
            horizontal=True
        )

        if upload_method == "Individual PDFs":
            current = st.session_state.get("uploaded_files", [])

            if not current:
                # Hidden native uploader (UI wrapped and hidden with CSS)
                st.markdown('<div class="upload-files-wrapper">', unsafe_allow_html=True)
                uploader = st.file_uploader(
                    "Select PDF files",
                    type=["pdf"],
                    accept_multiple_files=True,
                    label_visibility="collapsed",
                )
                st.markdown(
                    """
                    <style>
                    .upload-files-wrapper {
                        height: 0 !important;
                        margin: 0 !important;
                        padding: 0 !important;
                        overflow: hidden !important;
                    }
                    .st-emotion-cache-1atoy9e {
                        flex: 0 0 330px !important;
                    }
                    [data-testid="stFileUploader"] {
                        position: absolute !important;
                        width: 1px !important;
                        height: 1px !important;
                        opacity: 0 !important;
                        pointer-events: none !important;
                        overflow: hidden !important;
                        margin: 0 !important;
                        padding: 0 !important;
                    }
                    [data-testid="stFileUploadDropzone"] { display: none !important; }
                    </style>
                    """,
                    unsafe_allow_html=True,
                )
                st.markdown("</div>", unsafe_allow_html=True)

                if uploader:
                    st.session_state.uploaded_files = uploader
                    st.rerun()

                components.html(
                    """
                    <div id="custom-upload-zone" style="
                            border:1px dashed rgba(16, 78, 255, 0.18);
                            background: linear-gradient(180deg, #f3f7ff 0%, #eef6ff 100%);
                            border-radius:12px;
                            padding:28px 36px;
                            text-align:center;
                            cursor:pointer;
                            color:#0b1220;
                            min-height:200px;
                            font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                    ">
                        <div style="margin-bottom:8px;"><img src="https://www.svgrepo.com/show/302427/cloud-upload.svg" width=56 height=56 style="opacity:.95;"/></div>
                        <div style="margin:8px 0 6px 0;">
                            <span style="font-weight:700; color:#ffffff; background:#0066ff; padding:10px 28px; border-radius:8px; display:inline-block; font-size:16px; letter-spacing:0.1px;">＋&nbsp;&nbsp;Select files</span>
                        </div>
                        <div style="color:#0b1220; font-size:15px; margin-top:20px; font-weight:600;">Add PDF, image, Word, Excel, and <strong>PowerPoint</strong> files</div>
                        <div style="margin-top:10px; color:#334155; font-size:13px;">
                            Supported formats:
                            <span style="background:#fde8ea; color:#b91c1c; padding:4px 8px; margin-left:8px; border-radius:12px; font-weight:700; font-size:12px;">PDF</span>
                            <span style="background:#e6f7ff; color:#0b6b9a; padding:4px 8px; margin-left:6px; border-radius:12px; font-weight:700; font-size:12px;">DOC</span>
                            <span style="background:#ecfdf5; color:#047857; padding:4px 8px; margin-left:6px; border-radius:12px; font-weight:700; font-size:12px;">XLS</span>
                            <span style="background:#fff7ed; color:#b45309; padding:4px 8px; margin-left:6px; border-radius:12px; font-weight:700; font-size:12px;">PPT</span>
                            <span style="background:#fff9db; color:#b45309; padding:4px 8px; margin-left:6px; border-radius:12px; font-weight:700; font-size:12px;">PNG</span>
                            <span style="background:#fff1d6; color:#92400e; padding:4px 8px; margin-left:6px; border-radius:12px; font-weight:700; font-size:12px;">JPG</span>
                        </div>
                    </div>
                    <script>
                        const tryBind = () => {
                            const zone = document.getElementById('custom-upload-zone');
                            const input = window.parent.document.querySelector('input[type="file"]');
                            if (zone && input) {
                                zone.addEventListener('click', () => input.click());
                                // also add keyboard accessibility
                                zone.setAttribute('tabindex', 0);
                                zone.addEventListener('keydown', (e) => {
                                    if (e.key === 'Enter' || e.key === ' ') input.click();
                                });
                            } else {
                                setTimeout(tryBind, 250);
                            }
                        };
                        tryBind();
                    </script>
                    """,
                    height=200,
            )
            else:
                # Hidden uploader for adding more files
                if 'add_more_key' not in st.session_state:
                    st.session_state.add_more_key = 0
                
                # CSS to hide the uploader but keep it functional
                st.markdown(
                    """
                    <style>
                    /* Hide the uploader wrapper/container in this section */
                    /* We target the specific uploader by ensuring this style is only injected here */
                    div[data-testid="stFileUploader"] {
                        position: fixed !important;
                        top: 0 !important;
                        left: 0 !important;
                        width: 1px !important;
                        height: 1px !important;
                        opacity: 0 !important;
                        overflow: hidden !important;
                        z-index: -1 !important;
                        pointer-events: none !important;
                    }
                    div[data-testid="stFileUploadDropzone"] {
                        opacity: 0 !important;
                        height: 1px !important;
                        width: 1px !important;
                        overflow: hidden !important;
                    }
                    </style>
                    """, 
                    unsafe_allow_html=True
                )
                
                new_files = st.file_uploader(
                    "Add more files", 
                    type=["pdf"], 
                    accept_multiple_files=True, 
                    key=f"add_more_{st.session_state.add_more_key}",
                    label_visibility="collapsed"
                )

                if new_files:
                    current.extend(new_files)
                    st.session_state.uploaded_files = current
                    # Invalidate meta to trigger regeneration
                    if 'uploaded_meta' in st.session_state:
                        del st.session_state.uploaded_meta
                    st.session_state.add_more_key += 1
                    st.rerun()

                # Ensure metadata for uploaded files (rotation, pages)
                if 'uploaded_meta' not in st.session_state or len(st.session_state.uploaded_meta) != len(current):
                    meta = []
                    for f in current:
                        fname = getattr(f, 'name', str(f))
                        pages = ''
                        thumb_b64 = None
                        # Try to detect PDF pages if PyPDF2 available
                        try:
                            from PyPDF2 import PdfReader
                            f.seek(0)
                            reader = PdfReader(f)
                            pages = len(reader.pages)
                            # Generate thumbnail preview (respect rotation)
                            f.seek(0)
                            content = f.read()
                            f.seek(0)
                            try:
                                rot0 = 0
                                thumb_size0 = (180, 240)
                                # no per-file rotation stored yet; default to 0
                                thumb_b64 = generate_thumbnail(pdf_bytes=content, page=0, size=thumb_size0)
                            except Exception:
                                thumb_b64 = None
                            f.seek(0)
                        except Exception:
                            pages = ''
                        meta.append({'name': fname, 'rotation': 0, 'pages': pages, 'thumb': thumb_b64})
                    st.session_state.uploaded_meta = meta

                # Removed advanced toolbar and extra uploader to match pixel-spec UI

                files = st.session_state.get('uploaded_files', [])
                meta = st.session_state.get('uploaded_meta', [])

                # --- PREVIEW MODAL ---
                if st.session_state.get('preview_file_index') is not None:
                    idx = st.session_state.preview_file_index
                    if 0 <= idx < len(files):
                        with st.container():
                            col_p1, col_p2 = st.columns([0.9, 0.1])
                            with col_p1:
                                st.subheader(f"Preview: {meta[idx].get('name')}")
                            with col_p2:
                                if st.button("✖", key="close_preview"):
                                    st.session_state.preview_file_index = None
                                    st.rerun()
                            
                            # Prepare data for render_exhibit_preview
                            f = files[idx]
                            f.seek(0)
                            content = f.read()
                            f.seek(0)
                            
                            exhibit_data = {
                                'name': meta[idx].get('name'),
                                'page_count': meta[idx].get('pages'),
                                'thumbnail': meta[idx].get('thumb'),
                                'content': content,
                                'filename': meta[idx].get('name')
                            }
                            render_exhibit_preview(exhibit_data, idx)
                            st.divider()
                
                # --- View Mode Toggle ---
                if 'view_mode' not in st.session_state:
                    st.session_state.view_mode = 'files'

                # Custom Toolbar
                st.markdown("""
                <style>
                    /* Style for the toolbar container */
                    .toolbar-container {
                        display: flex;
                        align-items: center;
                        gap: 12px;
                        padding: 8px 0;
                        margin-bottom: 16px;
                    }
                    /* Hide default radio buttons */
                    div[data-testid="stRadio"] > div {
                        flex-direction: row;
                        gap: 0px;
                        background: #f2f5fb;
                        border: 1px solid #e4e9f2;
                        border-radius: 8px;
                        padding: 2px;
                    }
                    div[data-testid="stRadio"] label {
                        background: transparent;
                        padding: 6px 16px;
                        border-radius: 6px;
                        margin: 0;
                        border: none;
                        color: #64748B;
                        font-weight: 500;
                        cursor: pointer;
                        transition: all 0.2s;
                    }
                    div[data-testid="stRadio"] label[data-checked="true"] {
                        background: #eaf1ff;
                        color: #1064FF;
                        font-weight: 600;
                        box-shadow: 0 1px 2px rgba(0,0,0,0.05);
                    }
                    /* Style for Add button override */
                    button[kind="secondary"] {
                        border: 1px solid #e4e9f2;
                        color: #475569;
                    }
                </style>
                """, unsafe_allow_html=True)

                col_tb_1, col_tb_2, col_tb_3, col_tb_4, col_tb_5, col_tb_6, col_tb_7 = st.columns([0.15, 0.07, 0.03, 0.03, 0.03, 0.41, 0.2])
                
                with col_tb_1:
                    # View Toggle
                    view_mode = st.radio(
                        "View Mode",
                        ["📄 Files", "▦ Pages"],
                        index=0 if st.session_state.view_mode == 'files' else 1,
                        horizontal=True,
                        label_visibility="collapsed",
                        key="view_mode_selector"
                    )
                    # Update state based on selection
                    new_mode = 'files' if "Files" in view_mode else 'pages'
                    if new_mode != st.session_state.view_mode:
                        st.session_state.view_mode = new_mode
                        st.rerun()

                with col_tb_2:
                    # Add Button - This uses JS to trigger the hidden uploader
                    slot_html = """
                    <div id="add_slot" style="
                        display:flex; align-items:center; gap:6px;
                        padding:6px 12px; background:#fff; border:1px solid #e4e9f2;
                        border-radius:8px; color:#475569; font-size:14px; font-family:sans-serif;
                        cursor:pointer; width: fit-content; margin-top: -2px;
                    ">
                        ＋ Add <span style="font-size:10px">▼</span>
                    </div>
                    
                    """
                    st.markdown(slot_html, unsafe_allow_html=True)

                    bind = '''
                    <script>
                    (function(){
                        try {
                            const attach = () => {
                                try {
                                    const slot = window.parent.document.getElementById('add_slot');
                                    if (!slot) return false;
                                    slot.style.cursor = 'pointer';

                                    const isVisible = (el) => {
                                        try {
                                            const s = window.parent.getComputedStyle(el);
                                            if (!s) return false;
                                            if (s.display === 'none' || s.visibility === 'hidden') return false;
                                            return true;
                                        } catch(e) { return false; }
                                    };

                                    const findVisibleFileInput = () => {
                                        const inputs = Array.from(window.parent.document.querySelectorAll('input[type=file]'));
                                        for (let inp of inputs.reverse()) {
                                            try { if (isVisible(inp)) return inp; } catch(e) {}
                                        }
                                        return null;
                                    };

                                    const findAddButton = () => {
                                        const buttons = Array.from(window.parent.document.querySelectorAll('button'));
                                        for (let b of buttons) {
                                            try {
                                                const txt = (b.innerText || '').trim().toLowerCase();
                                                if (txt === 'add files' || txt.indexOf('add files') !== -1) return b;
                                            } catch(e) { }
                                        }
                                        return null;
                                    };

                                    const hideButtonVisually = (btn) => {
                                        try {
                                            // Move off-screen but keep it in the DOM so .click() works
                                            btn.style.position = 'absolute';
                                            btn.style.left = '-9999px';
                                            btn.style.top = '0';
                                            btn.style.opacity = '0';
                                            btn.style.zIndex = '0';
                                        } catch(e) {}
                                    };

                                    const clickTarget = () => {
                                        // Try visible file input first
                                        const inp = findVisibleFileInput();
                                        if (inp) {
                                            try { inp.click(); return true; } catch(e) {}
                                        }
                                        // Fallback: click Add files button (may be hidden visually but still clickable)
                                        const btn = findAddButton();
                                        if (btn) {
                                            try { hideButtonVisually(btn); btn.click(); return true; } catch(e) {}
                                        }
                                        return false;
                                    };

                                    slot.addEventListener('click', function(e){ e.preventDefault(); try { clickTarget(); } catch(err){} });
                                    return true;
                                } catch(err){ return false; }
                            };
                            if (!attach()){
                                let attempts = 0;
                                const intr = setInterval(()=>{ attempts+=1; if (attach()||attempts>12) clearInterval(intr); },250);
                            }
                        } catch(e){}
                    })();
                    </script>
                    '''
                    components.html(bind, height=0)

                with col_tb_3:
                    # Sort Button (Popover)
                    with st.popover("⇅", help="Sort files"):
                        sort_order = st.radio(
                            "Sort by",
                            ["Name, A-Z", "Name, Z-A"],
                            key="sort_files_radio",
                        )
                        if st.button("Apply", key="apply_sort_btn_toolbar"):
                            if files and meta and len(files) == len(meta):
                                reverse = (sort_order == "Name, Z-A")

                                # Build list of (orig_index, file_obj, meta_obj)
                                orig_items = [(i, f, m) for i, (f, m) in enumerate(zip(files, meta))]
                                sorted_items = sorted(orig_items, key=lambda x: (x[2].get('name') or '').lower(), reverse=reverse)

                                new_files = [item[1] for item in sorted_items]
                                new_meta = [item[2] for item in sorted_items]
                                perm = [item[0] for item in sorted_items]

                                # Only update if the order actually changed
                                current_names = [m.get('name') for m in meta]
                                new_names = [m.get('name') for m in new_meta]
                                if current_names != new_names:
                                    st.session_state.uploaded_files = new_files
                                    st.session_state.uploaded_meta = new_meta

                                    related_keys = ['exhibit_order', 'processed_files', 'exhibit_list']
                                    for k in related_keys:
                                        if k in st.session_state:
                                            try:
                                                old_list = list(st.session_state.get(k) or [])
                                                if len(old_list) == len(perm):
                                                    st.session_state[k] = [old_list[idx] for idx in perm]
                                            except Exception:
                                                pass

                                    def map_old_to_new(old_idx):
                                        for new_pos, orig_idx in enumerate(perm):
                                            if orig_idx == old_idx:
                                                return new_pos
                                        return None

                                    for sel_key in ('selected_upload_index', 'preview_file_index'):
                                        if sel_key in st.session_state and st.session_state.get(sel_key) is not None:
                                            try:
                                                old_sel = int(st.session_state.get(sel_key))
                                                new_sel = map_old_to_new(old_sel)
                                                if new_sel is not None:
                                                    st.session_state[sel_key] = new_sel
                                                else:
                                                    st.session_state.pop(sel_key, None)
                                            except Exception:
                                                st.session_state.pop(sel_key, None)

                                    dynamic_prefixes = (
                                        'preview_', 'move_mode_', 'view_card_', 'dup_card_', 'del_card_',
                                        'insert_here_', 'insert_files_', 'list_del_'
                                    )
                                    keys_to_clear = [k for k in list(st.session_state.keys()) if any(k.startswith(p) for p in dynamic_prefixes)]
                                    for k in keys_to_clear:
                                        try:
                                            st.session_state.pop(k, None)
                                        except Exception:
                                            pass

                                    st.rerun()

                with col_tb_4:
                    if st.button("↺", help="Rotate Left", disabled=False, key="btn_rotate_left"):
                        if meta:
                            uploaded_files = st.session_state.get('uploaded_files', [])
                            for i, m in enumerate(meta):
                                m['rotation'] = (m.get('rotation', 0) - 90) % 360
                                # Regenerate thumbnail to reflect rotation if file bytes available
                                try:
                                    if 0 <= i < len(uploaded_files):
                                        f = uploaded_files[i]
                                        f.seek(0)
                                        content = f.read()
                                        f.seek(0)
                                        try:
                                            rot_local = int(m.get('rotation', 0) or 0)
                                            thumb_size_local = (180, 240) if rot_local % 180 == 0 else (240, 180)
                                            new_thumb = generate_thumbnail(pdf_bytes=content, page=0, size=thumb_size_local, rotation=rot_local)
                                            if new_thumb:
                                                m['thumb'] = new_thumb
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                            st.session_state.uploaded_meta = meta
                            st.rerun()

                with col_tb_5:
                    if st.button("↻", help="Rotate Right", disabled=False, key="btn_rotate_right"):
                        if meta:
                            uploaded_files = st.session_state.get('uploaded_files', [])
                            for i, m in enumerate(meta):
                                m['rotation'] = (m.get('rotation', 0) + 90) % 360
                                # Regenerate thumbnail to reflect rotation if file bytes available
                                try:
                                    if 0 <= i < len(uploaded_files):
                                        f = uploaded_files[i]
                                        f.seek(0)
                                        content = f.read()
                                        f.seek(0)
                                        try:
                                            rot_local = int(m.get('rotation', 0) or 0)
                                            thumb_size_local = (180, 240) if rot_local % 180 == 0 else (240, 180)
                                            new_thumb = generate_thumbnail(pdf_bytes=content, page=0, size=thumb_size_local, rotation=rot_local)
                                            if new_thumb:
                                                m['thumb'] = new_thumb
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                            st.session_state.uploaded_meta = meta
                            st.rerun()

                with col_tb_7:
                    if st.button("Done →", type="primary", use_container_width=True):
                        navigator.next_stage()
                        st.rerun()

                # --- Render Content based on View Mode ---
                
                if st.session_state.view_mode == 'files':
                    # FILES VIEW (Existing Card Grid)
                    n = len(files)
                    selected_idx = st.session_state.get('selected_upload_index', 0)
                    
                    # Determine global rotation state from first item (assuming uniform rotation)
                    first_rotation = 0
                    if meta and len(meta) > 0:
                        first_rotation = meta[0].get('rotation', 0)
                    
                    is_landscape = (first_rotation % 180 != 0)
                    



                    # Render a Streamlit-native card grid using columns so action buttons are server-side


                    # Inject small card CSS (scoped visually) once
                    card_styles = """
                    <style>
                    /* Outer light-blue card with white inner panel look */
                    .card-wrapper { background: rgba(47,134,255,0.08); border-radius: 10px; padding: 5px; box-sizing: border-box; display:flex; flex-direction:column; align-items:center; justify-content:flex-start; min-height:300px; height: 450px; position:absolute ; width:217px; margin-top:10px; box-shadow:0 10px 24px rgba(2,6,23,0.06); }

                          /* Hide native Streamlit action row (we'll show a styled visual overlay instead) */
                          .card-actions-row { display:none !important }

                          /* Visual overlay actions (purely decorative) - placed at top center */
                          .visual-actions { position:absolute; top:10px; left:50%; transform:translateX(-50%); display:flex; gap:8px; align-items:center; z-index:30 }
                          .visual-actions .action-circle { width:36px; height:36px; border-radius:50%; background:white; display:flex; align-items:center; justify-content:center; box-shadow:0 6px 16px rgba(2,6,23,0.06); border:1px solid rgba(2,6,23,0.04); font-size:15px }
                          .visual-actions .action-circle.delete { color:#ef4444 }

                          /* Right-side floating plus visual */
                          .plus-visual { position:absolute; right:14px; top:50%; transform:translateY(-50%); width:44px; height:44px; border-radius:50%; background:#2f86ff; color:white; display:flex; align-items:center; justify-content:center; box-shadow:0 10px 24px rgba(47,134,255,0.18); font-size:20px; z-index:30 }

                    /* Thumbnail area: create a stacked/card-on-card layered look */
                    .card-thumb { width:188px; height:245px; display:flex; margin-left:38px; border-radius:8px; position:relative; margin-top:-30px }
                    .card-thumb .img-frame { position:absolute; left:0; top:0; right:0; bottom:0; display:flex; align-items:center; justify-content:center; z-index:2; overflow:hidden; background:#fff; border:1px solid #eef2f7 }
                    .card-thumb img { max-width:100%; max-height:100%; object-fit:contain; z-index: 5; margin-left: -20px; }
                    .st-emotion-cache-1permvm {
                            justify-content: space-between;
                    }
                    .st-emotion-cache-1j4it34 { flex:none; }
                    div[data-testid="stColumn"] { width:auto; flex: none; min-width: auto; }
                    .st-emotion-cache-ai037n { margin-bottom: 12px; }
                    /* Name and pages centered below thumbnail */
                    .card-name { color:#6b7280; font-size:12px; text-align:center; background: rgba(47,134,255,0.12); color:#0b5cff; padding:6px 12px; border-radius:12px; font-weight:600; position: absolute; top: 10px; left: 25px; width: 170px; }
                    .card-pages { font-weight:400; color:#a3a3a3; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; text-align:center; position:absolute; left:28%; top:25px; }
                    /* Ensure action row buttons inside Streamlit columns are compact */
                    .stButton>button { padding:6px 8px }
                    @media (max-width:900px) { 
                        .card-wrapper { min-height:300px }
                        .st-emotion-cache-1permvm {
                            justify-content: space-between;
                        }
                    }
                    @media (max-width:700px) {
                        .st-emotion-cache-1permvm {
                            justify-content: center;
                        }
                    }
                    @media (max-width:600px) {
                        .st-emotion-cache-1permvm {
                            justify-content: center;
                        }
                    }
                    
                    </style>
                    """
                    st.markdown(card_styles, unsafe_allow_html=True)

                    # Start horizontal scroll wrapper for cards
                    # Check if there's a short-lived insert preview to render at the insertion slot
                    insert_preview = st.session_state.get('last_insert_preview')

                    # Render cards row-by-row and always include one extra slot
                    # for the "Add" card so it's visible even when rows are full.
                    total_items = n + 1  # n cards + 1 add-slot
                    cols_per_row = total_items
                    rows = (total_items + cols_per_row - 1) // cols_per_row if total_items > 0 else 1
                    rendered_count = 0
                    for r in range(rows):
                        row_cols = st.columns(cols_per_row)
                        for c in range(cols_per_row):
                            i = r * cols_per_row + c
                            with row_cols[c]:
                                # If preview exists and its position matches current index, render the preview cards first
                                if insert_preview and insert_preview.get('pos') == i:
                                    for j, (pth, pname) in enumerate(zip(insert_preview.get('thumbs', []), insert_preview.get('names', []))):
                                        try:
                                            st.markdown('<div class="card-wrapper">', unsafe_allow_html=True)
                                            if pth:
                                                if isinstance(pth, str) and pth.startswith('PHN2'):
                                                    st.markdown(f'<div class="card-thumb"><img src="data:image/svg+xml;base64,{pth}"/></div>', unsafe_allow_html=True)
                                                else:
                                                    st.markdown(f'<div class="card-thumb"><img src="data:image/jpeg;base64,{pth}"/></div>', unsafe_allow_html=True)
                                            else:
                                                st.markdown('<div class="card-thumb">PDF PREVIEW</div>', unsafe_allow_html=True)
                                            st.markdown(f'<div class="card-name" title="{pname}">{pname}</div>', unsafe_allow_html=True)
                                            st.markdown('</div>', unsafe_allow_html=True)
                                        except Exception:
                                            pass

                                # If this index corresponds to an existing uploaded file, render its card
                                if i < n:
                                    rendered_count += 1
                                    try:
                                        m = meta[i]
                                    except Exception:
                                        m = {}
                                    name = m.get('name')
                                    pages = f"{m.get('pages')} pages" if m.get('pages') else ''
                                    display_name = f"{name[:20]}{'...' if name and len(name) > 20 else ''}" if name else f"Document {i+1}"
                                    thumb_b64 = m.get('thumb')

                                    # If thumbnail missing, try generating it from uploaded file bytes
                                    if not thumb_b64:
                                        try:
                                            uploaded_files = st.session_state.get('uploaded_files', [])
                                            if 0 <= i < len(uploaded_files):
                                                f_obj = uploaded_files[i]
                                                try:
                                                    f_obj.seek(0)
                                                    content = f_obj.read()
                                                    f_obj.seek(0)
                                                    rot_here = int(m.get('rotation', 0) or 0)
                                                    size_here = (180, 240) if rot_here % 180 == 0 else (240, 180)
                                                    gen = generate_thumbnail(pdf_bytes=content, page=0, size=size_here, rotation=rot_here)
                                                    if gen:
                                                        thumb_b64 = gen
                                                        m['thumb'] = gen
                                                        st.session_state.uploaded_meta = st.session_state.get('uploaded_meta', [])
                                                except Exception:
                                                    pass
                                        except Exception:
                                            pass

                                    rotation = m.get('rotation', 0)

                                    try:
                                        st.markdown('<div class="card-wrapper">', unsafe_allow_html=True)
                                        action_cols = st.columns([0.23, 0.25, 0.25, 0.23, 0.25])
                                        st.markdown('<div class="card-actions-row">', unsafe_allow_html=True)
                                        with action_cols[0]:
                                            if st.button('🔍︎', key=f'view_card_{i}', help='Preview'):
                                                if st.session_state.get('preview_file_index') == i:
                                                    st.session_state.preview_file_index = None
                                                else:
                                                    st.session_state.preview_file_index = i
                                                st.rerun()
                                        with action_cols[1]:
                                            if st.button('↻', key=f'rotate_card_{i}', help='Rotate'):
                                                rotate_file(i)
                                        with action_cols[2]:
                                            if st.button('⿻', key=f'dup_card_{i}', help='Duplicate'):
                                                duplicate_file(i)
                                        with action_cols[3]:
                                            if st.button('🗑', key=f'del_card_{i}', help='Delete'):
                                                delete_file(i)
                                        with action_cols[4]:
                                            if st.button('＋', key=f'insert_here_{i}', help='Insert files here'):
                                                st.session_state.insert_position = i + 1
                                                try:
                                                    uploaded_files_tmp = st.session_state.get('uploaded_files', [])
                                                    if 0 <= i < len(uploaded_files_tmp):
                                                        f_obj = uploaded_files_tmp[i]
                                                        anchor_size = getattr(f_obj, 'size', None)
                                                        anchor_name = getattr(f_obj, 'name', None)
                                                        st.session_state.insert_anchor = (anchor_name, anchor_size)
                                                        st.session_state.insert_anchor_index = i
                                                    else:
                                                        st.session_state.insert_anchor = None
                                                        st.session_state.insert_anchor_index = None
                                                except Exception:
                                                    st.session_state.insert_anchor = None
                                                    st.session_state.insert_anchor_index = None

                                                st.session_state.insert_uploader_key = st.session_state.get('insert_uploader_key', 0) + 1
                                                st.session_state.open_insert_uploader = True
                                                st.rerun()
                                        st.markdown('</div>', unsafe_allow_html=True)

                                        if thumb_b64:
                                            if isinstance(thumb_b64, str) and thumb_b64.startswith('PHN2'):
                                                st.markdown(f'<div class="card-thumb"><img src="data:image/svg+xml;base64,{thumb_b64}" alt="{display_name}"/></div>', unsafe_allow_html=True)
                                            else:
                                                st.markdown(f'<div class="card-thumb"><img src="data:image/jpeg;base64,{thumb_b64}" alt="{display_name}"/></div>', unsafe_allow_html=True)
                                        else:
                                            st.markdown('<div class="card-thumb">PDF PREVIEW</div>', unsafe_allow_html=True)

                                        st.markdown(f'<div class="card-name" title="{name}">{display_name}</div>', unsafe_allow_html=True)
                                        st.markdown(f'<div class="card-pages">{pages}</div>', unsafe_allow_html=True)
                                        st.markdown('</div>', unsafe_allow_html=True)
                                    except Exception:
                                        pass

                                # If this is the slot immediately after the last card, render add-slot here
                                elif i == n:
                                    # Render any insert_preview targeted at the end
                                    if insert_preview and insert_preview.get('pos') == n:
                                        for j, (pth, pname) in enumerate(zip(insert_preview.get('thumbs', []), insert_preview.get('names', []))):
                                            try:
                                                st.markdown('<div class="card-wrapper">', unsafe_allow_html=True)
                                                if pth:
                                                    if isinstance(pth, str) and pth.startswith('PHN2'):
                                                        st.markdown(f'<div class="card-thumb"><img src="data:image/svg+xml;base64,{pth}"/></div>', unsafe_allow_html=True)
                                                    else:
                                                        st.markdown(f'<div class="card-thumb"><img src="data:image/jpeg;base64,{pth}"/></div>', unsafe_allow_html=True)
                                                else:
                                                    st.markdown('<div class="card-thumb">PDF PREVIEW</div>', unsafe_allow_html=True)
                                                st.markdown(f'<div class="card-name" title="{pname}">{pname}</div>', unsafe_allow_html=True)
                                                st.markdown('</div>', unsafe_allow_html=True)
                                            except Exception:
                                                pass

                                    # Add the large add-slot button
                                    add_slot_html = '''
                                    <button id="large_add_slot" style="width:210px;height:450px;border-radius:12px;border:2px dashed #3B82F6;background:#eef6ff;display:flex;align-items:center;justify-content:center;color:#3B82F6;font-weight:600;text-align:center;padding:16px; margin-top: 20px">
                                        <div style="text-align:center;">
                                            <div style="width:40px;height:40px;border-radius:20px;border:2px solid #cfe3ff;display:inline-flex;align-items:center;justify-content:center;margin-bottom:12px;background:white;color:#3B82F6;font-size:24px">＋</div>
                                            <div style="color:#1064FF;font-weight:700;margin-top:6px">Add PDF,<br/>image, Word,<br/>Excel, and<br/><strong>PowerPoint</strong><br/>files</div>
                                        </div>
                                    </button>
                                    '''
                                    st.markdown(add_slot_html, unsafe_allow_html=True)

                                    add_slot_css = '''
                                    <style>
                                    @media (max-width: 900px) {
                                        #add_slot {
                                            width: 14.5rem !important;
                                            justify-content: center;
                                            margin: auto;
                                        }
                                    }
                                    </style>
                                    '''
                                    st.markdown(add_slot_css, unsafe_allow_html=True)

                                    # JS bridge to trigger the Streamlit file input reliably.
                                    # Prefer clicking visible file inputs; if none, click the (possibly hidden) "Add files" button.
                                    bind_js = '''
                                    <script>
                                    (function(){
                                        try {
                                            const attach = () => {
                                                try {
                                                    const slot = window.parent.document.getElementById('large_add_slot');
                                                    if (!slot) return false;
                                                    slot.style.cursor = 'pointer';

                                                    const isVisible = (el) => {
                                                        try {
                                                            const s = window.parent.getComputedStyle(el);
                                                            if (!s) return false;
                                                            if (s.display === 'none' || s.visibility === 'hidden') return false;
                                                            return true;
                                                        } catch(e) { return false; }
                                                    };

                                                    const findVisibleFileInput = () => {
                                                        const inputs = Array.from(window.parent.document.querySelectorAll('input[type=file]'));
                                                        for (let inp of inputs.reverse()) {
                                                            try { if (isVisible(inp)) return inp; } catch(e) {}
                                                        }
                                                        return null;
                                                    };

                                                    const findAddButton = () => {
                                                        const buttons = Array.from(window.parent.document.querySelectorAll('button'));
                                                        for (let b of buttons) {
                                                            try {
                                                                const txt = (b.innerText || '').trim().toLowerCase();
                                                                if (txt === 'add files' || txt.indexOf('add files') !== -1) return b;
                                                            } catch(e) { }
                                                        }
                                                        return null;
                                                    };

                                                    const hideButtonVisually = (btn) => {
                                                        try {
                                                            // Move off-screen but keep it in the DOM so .click() works
                                                            btn.style.position = 'absolute';
                                                            btn.style.left = '-9999px';
                                                            btn.style.top = '0';
                                                            btn.style.opacity = '0';
                                                            btn.style.zIndex = '0';
                                                        } catch(e) {}
                                                    };

                                                    const clickTarget = () => {
                                                        // Try visible file input first
                                                        const inp = findVisibleFileInput();
                                                        if (inp) {
                                                            try { inp.click(); return true; } catch(e) {}
                                                        }
                                                        // Fallback: click Add files button (may be hidden visually but still clickable)
                                                        const btn = findAddButton();
                                                        if (btn) {
                                                            try { hideButtonVisually(btn); btn.click(); return true; } catch(e) {}
                                                        }
                                                        return false;
                                                    };

                                                    slot.addEventListener('click', function(e){ e.preventDefault(); try { clickTarget(); } catch(err){} });
                                                    return true;
                                                } catch(err){ return false; }
                                            };
                                            if (!attach()){
                                                let attempts = 0;
                                                const intr = setInterval(()=>{ attempts+=1; if (attach()||attempts>12) clearInterval(intr); },250);
                                            }
                                        } catch(e){}
                                    })();
                                    </script>
                                    '''
                                    components.html(bind_js, height=0)

                                    # if st.button('Add files', key='add_slot_append'):
                                    #     st.session_state.insert_position = n
                                    #     st.session_state.insert_uploader_key = st.session_state.get('insert_uploader_key', 0) + 1
                                    #     st.session_state.open_insert_uploader = True
                                    #     st.rerun()
                                else:
                                    # Empty placeholder
                                    st.write('')

                    # Clear last_insert_preview so it only appears once
                    if insert_preview:
                        st.session_state.pop('last_insert_preview', None)

                    # Close horizontal scroll wrapper
                    st.markdown('</div>', unsafe_allow_html=True)

                    # Duplicate end-slot removed — the add-slot is rendered inline above (row-by-row).

                
                else:
                    # PAGES VIEW (New Implementation)
                    # We need to render every page of every PDF
                    # This could be resource intensive, so we limit or paginate if necessary, but request says "display all pages"
                    
                    # 1. Collect all pages
                    all_pages = []
                    for i, f in enumerate(files):
                        m = meta[i]
                        num_pages = int(m.get('pages', 0)) if m.get('pages') else 0
                        
                        # Cache key for this file
                        # Use name + size to be more unique than just name
                        file_id = f"{f.name}_{f.size}"
                        
                        # We need to read the file content to generate thumbnails
                        f.seek(0)
                        bytes_content = f.read()
                        
                        for p_idx in range(num_pages):
                            # Check if we have this thumb in session state cache? 
                            # For now, generate on fly or use a simple cache key
                            cache_key = f"thumb_{file_id}_{p_idx}"
                            if cache_key not in st.session_state:
                                try:
                                    t = generate_thumbnail(pdf_bytes=bytes_content, page=p_idx, size=(150, 200))
                                    st.session_state[cache_key] = t
                                except:
                                    st.session_state[cache_key] = None
                            
                            thumb = st.session_state[cache_key]
                            all_pages.append({
                                'file_index': i,
                                'file_name': m.get('name'),
                                'page_index': p_idx,
                                'thumb': thumb,
                                'total_pages': num_pages
                            })
                    
                    # 2. Render Grid of Pages
                    # Similar CSS but simpler cards
                    
                    page_html = ['<div class="pages-grid">']
                    for item in all_pages:
                        thumb_b64 = item['thumb']
                        thumb_img = (
                            f'<img src="data:image/jpeg;base64,{thumb_b64}" />'
                            if thumb_b64 else '<div class="no-thumb">Page ' + str(item['page_index']+1) + '</div>'
                        )
                        
                        card = f"""
                        <div class="page-card">
                            <div class="page-preview">
                                {thumb_img}
                                <div class="page-number">{item['page_index'] + 1}</div>
                            </div>
                            <div class="file-label">{item['file_name']}</div>
                        </div>
                        """
                        # Add plus dot between pages? The screenshot shows plus dots between files, 
                        # but typically page view is just a grid. 
                        # The third screenshot shows + buttons between pages.
                        page_html.append(card)
                        page_html.append('<div class="plus-dot-small">+</div>')

                    # Remove last plus dot
                    if page_html and page_html[-1] == '<div class="plus-dot-small">+</div>':
                        page_html.pop()
                        
                    page_html.append('</div>')
                    
                    styles = """
                    <style>
                    .pages-grid {
                        display: flex;
                        flex-wrap: wrap;
                        gap: 16px;
                        align-items: center;
                        padding: 16px;
                        font-family: Inter, sans-serif;
                    }
                    .page-card {
                        display: flex;
                        flex-direction: column;
                        align-items: center;
                        width: 160px;
                    }
                    .page-preview {
                        width: 140px;
                        height: 190px;
                        background: #fff;
                        border: 1px solid #e5eaf2;
                        border-radius: 4px;
                        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        position: relative;
                        overflow: hidden;
                        margin-bottom: 8px;
                    }
                    .page-preview img {
                        width: 100%;
                        height: 100%;
                        object-fit: contain;
                    }
                    .page-number {
                        position: absolute;
                        bottom: 4px;
                        right: 4px;
                        background: rgba(0,0,0,0.5);
                        color: #fff;
                        font-size: 10px;
                        padding: 2px 6px;
                        border-radius: 4px;
                    }
                    .file-label {
                        font-size: 11px;
                        color: #64748B;
                        text-align: center;
                        max-width: 100%;
                        overflow: hidden;
                        text-overflow: ellipsis;
                        white-space: nowrap;
                    }
                    .plus-dot-small {
                        width: 24px; 
                        height: 24px; 
                        border-radius: 50%; 
                        background: #cfe3ff; 
                        color: #fff; 
                        display: flex; 
                        align-items: center; 
                        justify-content: center; 
                        font-size: 16px;
                    }
                    </style>
                    """
                    
                    components.html(styles + "\n".join(page_html), height=600, scrolling=True)


            #     st.markdown("""
            #     <script>
            #         const bindUpload = () => {{
            #             const slot = document.getElementById('upload-slot');
            #             const input = window.parent.document.querySelector('input[type="file"]');
            #             if (slot && input) {{
            #                 slot.addEventListener('click', () => input.click());
            #                 slot.style.cursor = 'pointer';
            #             }} else {{
            #                 setTimeout(bindUpload, 250);
            #             }}
            #         }};
            #         bindUpload();
            #     </script>
            # """, height=600)

                if st.session_state.get('insert_position') is not None:
                    pos = st.session_state.insert_position
                    # Create an insert uploader (auto-opened when triggered)
                    # Use a dynamic key so a fresh uploader is rendered each time the user clicks a plus
                    insert_key = st.session_state.get('insert_uploader_key', 0)
                    new_files = st.file_uploader("Select files to insert", accept_multiple_files=True, key=f"insert_files_{insert_key}")
                    # If requested, auto-click the newly rendered file input to open OS file dialog
                    if st.session_state.get('open_insert_uploader'):
                        js = """
                        <script>
                        (function(){
                            try {
                                // Find all file inputs in parent document and click the last one
                                const inputs = window.parent.document.querySelectorAll('input[type=file]');
                                if (inputs && inputs.length) {
                                    const el = inputs[inputs.length - 1];
                                    el.click();
                                }
                            } catch (e) { console.error('auto-open upload failed', e); }
                        })();
                        </script>
                        """
                        components.html(js, height=0)
                        st.session_state.open_insert_uploader = False
                    if new_files:
                        uploaded = list(st.session_state.get('uploaded_files', []))
                        meta = list(st.session_state.get('uploaded_meta', []))
                        insert_at = int(pos)

                        # Build lists for the new files (make stable in-memory copies)
                        inserted_files = []
                        inserted_meta = []
                        insert_thumbs = []
                        insert_names = []

                        for af in new_files:
                            try:
                                af.seek(0)
                                content = af.read()
                            except Exception:
                                content = None

                            # Create a stable in-memory copy so the object survives Streamlit lifecycle
                            if content is not None:
                                buf = io.BytesIO(content)
                                buf.name = getattr(af, 'name', str(af))
                                buf.size = len(content)
                            else:
                                buf = af

                            # Generate thumbnail and page count
                            thumb_b64 = None
                            pages = ''
                            try:
                                from PyPDF2 import PdfReader
                                if content is not None:
                                    reader = PdfReader(io.BytesIO(content))
                                else:
                                    af.seek(0)
                                    reader = PdfReader(af)
                                pages = len(reader.pages)
                            except Exception:
                                pages = ''

                            try:
                                # Respect rotation when generating thumbnail (swap size for 90/270)
                                rot_for_thumb = 0
                                try:
                                    rot_for_thumb = int(getattr(af, 'rotation', 0) or 0)
                                except Exception:
                                    rot_for_thumb = 0
                                thumb_size = (180, 240) if rot_for_thumb % 180 == 0 else (240, 180)
                                if content is not None:
                                    thumb_b64 = generate_thumbnail(pdf_bytes=content, page=0, size=thumb_size, rotation=rot_for_thumb)
                                else:
                                    af.seek(0)
                                    thumb_b64 = generate_thumbnail(pdf_bytes=af.read(), page=0, size=thumb_size, rotation=rot_for_thumb)
                                    af.seek(0)
                            except Exception:
                                thumb_b64 = None

                            inserted_files.append(buf)
                            nm = getattr(af, 'name', str(af))
                            inserted_meta.append({'name': nm, 'rotation': 0, 'pages': pages, 'thumb': thumb_b64})
                            insert_thumbs.append(thumb_b64)
                            insert_names.append(nm)

                            # Do not dedupe: keep existing uploaded files as distinct entries.
                            cleaned_uploaded = list(uploaded)
                            cleaned_meta = list(meta)

                        # If an anchor was stored when the user clicked the plus,
                        # try to locate that anchor in the cleaned list so we insert
                        # at the correct position even if dedupe/reordering occurred.
                        anchor = st.session_state.get('insert_anchor')
                        anchor_index = st.session_state.get('insert_anchor_index')
                        if anchor is not None:
                            # Find first index in cleaned_uploaded matching the anchor key
                            # The UI places the circular "+" button after the card, so
                            # the new file(s) should appear after the clicked card.
                            found_idx = None
                            for idx_existing, f_obj in enumerate(cleaned_uploaded):
                                try:
                                    k_name = getattr(f_obj, 'name', None)
                                    k_size = getattr(f_obj, 'size', None)
                                    if (k_name, k_size) == anchor:
                                        found_idx = idx_existing
                                        break
                                except Exception:
                                    continue
                            if found_idx is not None:
                                # Insert after the matched index so the original item shifts right
                                insert_at = found_idx + 1
                            else:
                                # Fallback to numeric anchor index (+1) so we insert after that slot
                                if anchor_index is not None:
                                    insert_at = max(0, min(len(cleaned_uploaded), int(anchor_index) + 1))
                                else:
                                    insert_at = max(0, min(len(cleaned_uploaded), insert_at))
                        else:
                            # Clamp insert_at to cleaned list length
                            insert_at = max(0, min(len(cleaned_uploaded), insert_at))

                        # Clear anchor and anchor_index after use
                        if 'insert_anchor' in st.session_state:
                            st.session_state.pop('insert_anchor', None)
                        if 'insert_anchor_index' in st.session_state:
                            st.session_state.pop('insert_anchor_index', None)

                        # Rebuild lists using slicing so positions are deterministic
                        new_uploaded = cleaned_uploaded[:insert_at] + inserted_files + cleaned_uploaded[insert_at:]
                        new_meta = cleaned_meta[:insert_at] + inserted_meta + cleaned_meta[insert_at:]

                        st.session_state.uploaded_files = new_uploaded
                        st.session_state.uploaded_meta = new_meta

                        # Clear preview/selection state so UI keys remap cleanly after insertion
                        if 'preview_file_index' in st.session_state:
                            st.session_state.pop('preview_file_index', None)
                        if 'selected_upload_index' in st.session_state:
                            st.session_state.pop('selected_upload_index', None)

                        # Close the insert uploader and clear the insert_position
                        st.session_state.pop('insert_position', None)
                        st.session_state.open_insert_uploader = False

                        # To avoid Streamlit widget key collisions and transient UI duplication
                        # clear per-card dynamic keys so widget mapping remaps cleanly on rerun.
                        dynamic_prefixes = (
                            'preview_', 'move_mode_', 'view_card_', 'dup_card_', 'del_card_',
                            'insert_here_', 'insert_files_', 'list_del_', 'insert_uploader_key'
                        )
                        keys_to_clear = [k for k in list(st.session_state.keys()) if any(k.startswith(p) for p in dynamic_prefixes)]
                        for k in keys_to_clear:
                            try:
                                st.session_state.pop(k, None)
                            except Exception:
                                pass

                        # Select the first of the newly inserted files so the UI focuses it
                        try:
                            st.session_state.selected_upload_index = int(insert_at)
                        except Exception:
                            st.session_state.selected_upload_index = None

                        # Persist changes and rerun to render stable state
                        st.rerun()

        elif upload_method == "ZIP Archive":
            zip_file = st.file_uploader("Select ZIP file", type=["zip"])
            if zip_file:
                with tempfile.TemporaryDirectory() as tmp_dir:
                    zip_path = os.path.join(tmp_dir, "upload.zip")
                    with open(zip_path, 'wb') as f:
                        f.write(zip_file.read())
                    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                        for member in zip_ref.namelist():
                            if ".." in member or member.startswith("/"):
                                continue  # Skip dangerous paths
                            zip_ref.extract(member, tmp_dir)
                        pdf_files = list(Path(tmp_dir).rglob("*.pdf"))
                        st.info(f"Found {len(pdf_files)} PDF files in ZIP")
                        st.session_state.zip_files = [str(p) for p in pdf_files]

    with tab2:
        url_list = render_url_manager()

    with tab3:
        st.subheader("Google Drive Integration")
        st.info("💡 Connect to Google Drive to process folders directly")

        drive_url = st.text_input(
            "Google Drive Folder URL",
            placeholder="https://drive.google.com/drive/folders/..."
        )

        if drive_url:
            st.warning("🚧 Google Drive OAuth integration - coming in next update")

    st.markdown('</div>', unsafe_allow_html=True)

    # Check if we have files to proceed
    has_files = (
        len(st.session_state.get('uploaded_files', [])) > 0 or
        len(st.session_state.get('zip_files', [])) > 0 or
        len(get_url_list()) > 0
    )

    # Navigation buttons removed as per user request (replaced by "Done" button in toolbar)
    # navigator.render_navigation_buttons(
    #    next_label="Move to Classification",
    #    next_disabled=not has_files
    # )


def render_stage_3_classify(navigator: StageNavigator, config: Dict):
    """Stage 3: AI Classification"""

    st.markdown('<div class="stage-container">', unsafe_allow_html=True)

    render_context_summary()

    # Get files to classify
    files = st.session_state.get('uploaded_files', [])
    zip_files = st.session_state.get('zip_files', [])

    if not files and not zip_files:
        st.warning("No files to classify. Go back to upload stage.")
        navigator.render_navigation_buttons()
        return

    # Check if already classified
    classifications = get_classifications()

    if not classifications:
        # Run classification
        st.subheader("🤖 Classifying Documents...")

        api_key = st.session_state.get('anthropic_api_key')
        classifier = AIClassifier(api_key=api_key)

        all_classifications = []
        total_files = len(files) + len(zip_files)

        progress_bar = st.progress(0)
        status_text = st.empty()

        # Process uploaded files
        for i, file in enumerate(files):
            status_text.text(f"Classifying: {file.name}")

            # Read file content
            content = file.read()
            file.seek(0)  # Reset for later use

            result = classifier.classify_document(
                pdf_content=content,
                filename=file.name,
                visa_type=config['visa_type'],
                document_id=f"file_{i}"
            )
            all_classifications.append(result)
            progress_bar.progress((i + 1) / total_files)

        # Process zip files
        for i, file_path in enumerate(zip_files):
            filename = os.path.basename(file_path)
            status_text.text(f"Classifying: {filename}")

            with open(file_path, 'rb') as f:
                content = f.read()

            result = classifier.classify_document(
                pdf_content=content,
                filename=filename,
                visa_type=config['visa_type'],
                document_id=f"zip_{i}"
            )
            all_classifications.append(result)
            progress_bar.progress((len(files) + i + 1) / total_files)

        status_text.text("✓ Classification complete!")
        save_classifications(all_classifications)
        classifications = all_classifications

    # Show classification UI
    updated = render_classification_ui(classifications, config['visa_type'])
    save_classifications(updated)

    st.markdown('</div>', unsafe_allow_html=True)

    navigator.render_navigation_buttons(
        next_label="Review Classification"
    )

def render_stage_4_review(navigator: StageNavigator, config: Dict):
    """Stage 4: Manual Review & Reorder"""

    st.markdown('<div class="stage-container">', unsafe_allow_html=True)

    render_context_summary()

    # Convert classifications to exhibits if not done
    classifications = get_classifications()
    exhibits = get_exhibits()

    if classifications and not exhibits:
        set_exhibits_from_classifications(classifications, config['numbering_style'])

    # Render editor
    updated_exhibits = render_exhibit_editor(config['numbering_style'])

    st.markdown('</div>', unsafe_allow_html=True)

    navigator.render_navigation_buttons(
        next_label="Generate Exhibits",
        next_disabled=len(updated_exhibits) == 0
    )


def render_stage_5_generate(navigator: StageNavigator, config: Dict):
    """Stage 5: Background Processing"""
    st.markdown('<div class="stage-container">', unsafe_allow_html=True)

    render_context_summary()

    processor = get_processor()

    # If a previous run failed, surface the error and allow retry
    if processor.has_error:
        render_processing_ui()
        state = processor.state
        st.error(f"Generation failed: {state.error_message or 'Unknown error occurred during exhibit generation.'}")

        if st.button("Retry Generate Exhibit Package", type="primary", use_container_width=True):
            generate_exhibits_v2(config)
    
    elif not processor.is_running and not processor.is_complete:
        # Initial state: ready to start processing
        st.info("Click below to generate your exhibit package")

        if st.button("🚀 Generate Exhibit Package", type="primary", use_container_width=True):
            # Start background processing and immediately rerun so the
            # next render enters the `processor.is_running` branch and
            # shows the progress UI.
            generate_exhibits_v2(config)
            st.rerun()

    elif processor.is_running:
        # Show progress
        result = render_processing_ui()
        if result:
            st.session_state.processing_complete = True
            navigator.next_stage()
            st.rerun()

    elif processor.is_complete:
        # Transfer results from background processor to session state
        if hasattr(processor.state, 'result') and processor.state.result:
            result = processor.state.result
            st.session_state.output_file = result.get('output_file')
            st.session_state.exhibit_list = result.get('exhibit_list', [])
            st.session_state.cover_letter_path = result.get('cover_letter_path')
            st.session_state.filing_instructions_path = result.get('filing_instructions_path')
            
            # Transfer compression stats if available
            if 'compressed_size' in result and 'original_size' in result:
                st.session_state.compression_stats = {
                    'original_size': result['original_size'],
                    'compressed_size': result['compressed_size'],
                    'avg_reduction': result.get('avg_reduction', 0),
                    'method': result.get('compression_method', 'unknown'),
                    'quality': config.get('quality_preset', 'medium')
                }
        
        st.success("✓ Generation complete!")
        navigator.next_stage()
        st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)

    # Don't show nav buttons while processing
    if not processor.is_running:
        navigator.render_navigation_buttons()
    

def render_stage_6_complete(navigator: StageNavigator, config: Dict):
    """Stage 6: Download & Share"""
    st.markdown('<div class="stage-container">', unsafe_allow_html=True)

    st.markdown('<div class="success-box">✓ Your exhibit package is ready!</div>', unsafe_allow_html=True)

    # Statistics
    if st.session_state.get('exhibit_list'):
        st.subheader("📊 Statistics")

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric("Exhibits", len(st.session_state.exhibit_list))

        with col2:
            total_pages = sum(ex.get('pages', 0) for ex in st.session_state.exhibit_list)
            st.metric("Total Pages", total_pages)

        with col3:
            if st.session_state.compression_stats:
                reduction = st.session_state.compression_stats.get('avg_reduction', 0)
                st.metric("Size Reduction", f"{reduction:.1f}%")
            else:
                st.metric("Size Reduction", "-")

        with col4:
            if st.session_state.compression_stats:
                size_mb = st.session_state.compression_stats.get('compressed_size', 0) / (1024*1024)
                st.metric("Final Size", f"{size_mb:.1f} MB")
            else:
                st.metric("Final Size", "-")

    st.divider()
    # Download section
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("📥 Download")
        if st.session_state.get('output_file') and os.path.exists(st.session_state.output_file):
            # Read exhibit package bytes to ensure reliable download
            with open(st.session_state.output_file, 'rb') as f:
                package_bytes = f.read()
            case_context = get_case_context()
            beneficiary = case_context.beneficiary_name or "Package"
            st.download_button(
                label="📥 Download Exhibit Package",
                data=package_bytes,
                file_name=f"Exhibit_Package_{beneficiary}_{datetime.now().strftime('%Y%m%d')}.pdf",
                mime="application/pdf",
                type="primary",
                use_container_width=True
            )

            # Shareable link
            st.divider()
            render_link_generator(st.session_state.output_file)

            # Cover letter download (if generated)
            cover_path = st.session_state.get('cover_letter_path')
            if cover_path and os.path.exists(cover_path):
                with open(cover_path, 'rb') as cf:
                    cover_bytes = cf.read()
                st.download_button(
                    label="📄 Download Cover Letter",
                    data=cover_bytes,
                    file_name=f"Cover_Letter_{beneficiary}_{datetime.now().strftime('%Y%m%d')}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    type="secondary",
                    use_container_width=True
                )

            # Filing instructions (DIY) download
            filing_path = st.session_state.get('filing_instructions_path') or result.get('filing_instructions_path') if 'result' in locals() else None
            if not filing_path:
                # also check session state directly
                filing_path = st.session_state.get('filing_instructions_path')

            if filing_path and os.path.exists(filing_path):
                with open(filing_path, 'rb') as ff:
                    filing_bytes = ff.read()
                st.download_button(
                    label="🧾 Download Filing Instructions (DIY)",
                    data=filing_bytes,
                    file_name=f"Filing_Instructions_{beneficiary}_{datetime.now().strftime('%Y%m%d')}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    type="secondary",
                    use_container_width=True
                )

            # Comparable Evidence (CE) letter UI
            with st.expander("🧾 Comparable Evidence Letter (O-1A/O-1B/EB-1A)", expanded=False):
                st.caption("Generate an explanation letter when a standard criterion does not readily apply.")
                ce_criterion = st.text_input("Criterion letter (A, B, C, ...)", max_chars=1, key="ce_criterion")
                ce_reason = st.text_area("Why the standard criterion does not apply", key="ce_reason")
                ce_evidence = st.text_area("Describe the comparable evidence being submitted", key="ce_evidence")

                # Show previously generated CE letters
                ce_paths = st.session_state.get('ce_letter_paths', {}) or {}
                if ce_paths:
                    for k, p in ce_paths.items():
                        if os.path.exists(p):
                            with open(p, 'rb') as _f:
                                _b = _f.read()
                            st.download_button(
                                label=f"📄 Download CE Letter ({k})",
                                data=_b,
                                file_name=os.path.basename(p),
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                use_container_width=True
                            )

                if st.button("Generate CE Letter", type="secondary", use_container_width=True, key="gen_ce"):
                    if not ce_criterion:
                        st.error("Please enter a criterion letter (e.g., A).")
                    elif not ce_reason or not ce_evidence:
                        st.error("Please provide both a reason and the comparable evidence description.")
                    else:
                        try:
                            from templates.docx_engine import generate_ce_letter

                            case_context = get_case_context()
                            case_data = {
                                'beneficiary_name': getattr(case_context, 'beneficiary_name', None) or 'Beneficiary',
                                'petitioner_name': getattr(case_context, 'petitioner_name', None) or 'Petitioner',
                                'visa_type': config.get('visa_type') or getattr(case_context, 'visa_category', None) or 'O-1A',
                                'service_center': getattr(case_context, 'service_center', None) or 'California Service Center',
                            }

                            crit = ce_criterion.strip().upper()
                            tmp_ce = os.path.join(tempfile.gettempdir(), f"CE_Letter_{beneficiary}_{crit}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx")
                            generate_ce_letter(case_data, crit, ce_reason, ce_evidence, tmp_ce)

                            # Save path in session state keyed by criterion
                            ce_paths = st.session_state.get('ce_letter_paths', {}) or {}
                            ce_paths[crit] = tmp_ce
                            st.session_state.ce_letter_paths = ce_paths

                            # Read bytes and show immediate download
                            with open(tmp_ce, 'rb') as _f:
                                ce_bytes = _f.read()

                            st.success(f"CE letter for Criterion {crit} generated.")
                            st.download_button(
                                label=f"📄 Download CE Letter ({crit})",
                                data=ce_bytes,
                                file_name=os.path.basename(tmp_ce),
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                use_container_width=True,
                            )

                        except Exception as e:
                            st.error(f"Error generating CE letter: {e}")
                            import traceback
                            traceback.print_exc()

            # Legal brief: if already generated, show download; otherwise offer Generate button
            brief_path = st.session_state.get('legal_brief_path')
            if brief_path and os.path.exists(brief_path):
                with open(brief_path, 'rb') as bf:
                    brief_bytes = bf.read()
                st.download_button(
                    label="📘 Download Legal Brief",
                    data=brief_bytes,
                    file_name=f"Legal_Brief_{beneficiary}_{datetime.now().strftime('%Y%m%d')}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    type="secondary",
                    use_container_width=True
                )
            else:
                if st.button("🖋️ Generate Legal Brief", type="secondary", use_container_width=True):
                    try:
                        from templates.docx_engine import generate_legal_brief

                        case_context = get_case_context()
                        case_data = {
                            'beneficiary_name': getattr(case_context, 'beneficiary_name', None) or 'Beneficiary',
                            'petitioner_name': getattr(case_context, 'petitioner_name', None) or 'Petitioner',
                            'visa_type': config.get('visa_type') or getattr(case_context, 'visa_category', None) or 'O-1A',
                            'nationality': getattr(case_context, 'nationality', None) or '',
                            'field': getattr(case_context, 'field', None) or '',
                            'job_title': getattr(case_context, 'job_title', None) or '',
                            'duration': getattr(case_context, 'duration', None) or '3 years',
                            'processing_type': getattr(case_context, 'processing_type', None) or 'Regular',
                            'filing_fee': getattr(case_context, 'filing_fee', None) or '$460',
                            'premium_fee': getattr(case_context, 'premium_fee', None) or '$2,805',
                            'criteria_met': getattr(case_context, 'criteria_met', []) or []
                        }

                        exhibits = st.session_state.get('exhibit_list', [])

                        analyses = st.session_state.get('criterion_analyses') or {}
                        if not analyses:
                            claimed = case_data.get('criteria_met') or []
                            if not claimed:
                                claimed = ['A', 'C', 'F']
                            for i, letter in enumerate(claimed):
                                ex = None
                                if i < len(exhibits):
                                    ex = exhibits[i].get('title') or exhibits[i].get('name') or exhibits[i].get('filename')
                                ex_ref = f"See Exhibit {ex}" if ex else "See attached exhibits"
                                analyses[letter] = f"Analysis for criterion {letter}. {ex_ref}. (Auto-generated placeholder.)"

                        tmp_path = os.path.join(tempfile.gettempdir(), f"Legal_Brief_{beneficiary}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx")
                        generate_legal_brief(case_data, exhibits, analyses, tmp_path)

                        with open(tmp_path, 'rb') as bf:
                            brief_bytes = bf.read()

                        st.session_state.legal_brief_path = tmp_path
                        st.success("Legal brief generated — the download will begin below.")
                        st.download_button(
                            label="📘 Download Legal Brief",
                            data=brief_bytes,
                            file_name=f"Legal_Brief_{beneficiary}_{datetime.now().strftime('%Y%m%d')}.docx",
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            type="primary",
                            use_container_width=True,
                        )

                    except Exception as e:
                        st.error(f"Error generating legal brief: {e}")
                        import traceback
                        traceback.print_exc()

        else:
            st.warning("Output file not found. Try regenerating.")

    with col2:
        st.subheader("📧 Share")

        case_context = get_case_context()
        case_info = {
            'beneficiary_name': case_context.beneficiary_name or 'N/A',
            'petitioner_name': case_context.petitioner_name or 'N/A',
            'visa_type': config['visa_type'],
            'processing_type': case_context.processing_type or 'Regular',
            'exhibit_count': len(st.session_state.get('exhibit_list', [])),
            'page_count': sum(ex.get('pages', 0) for ex in st.session_state.get('exhibit_list', []))
        }

        render_email_form(
            case_info=case_info,
            file_path=st.session_state.get('output_file'),
            download_link=None  # Would be shareable link URL
        )

    st.markdown('</div>', unsafe_allow_html=True)

    navigator.render_navigation_buttons()


def generate_exhibits_v2(config: Dict):
    """Generate exhibits with V2 processing"""
    processor = get_processor()
    processor.reset()

    # Get files
    files = st.session_state.get('uploaded_files', [])
    zip_files = st.session_state.get('zip_files', [])
    exhibits = get_exhibits()

    def process_func(proc: BackgroundProcessor) -> Dict[str, Any]:
        """Background processing function"""
        import time

        result = {
            'exhibits': [],
            'total_pages': 0,
            'original_size': 0,
            'compressed_size': 0,
            'output_file': None
        }

        # Create temp directory
        tmp_dir = tempfile.mkdtemp()

        try:
            # Step 1: Extract/Save files
            proc.update_step("extract", "running")
            file_paths = []

            for i, file in enumerate(files):
                file_path = os.path.join(tmp_dir, file.name)
                with open(file_path, 'wb') as f:
                    f.write(file.read())
                file_paths.append(file_path)
                proc.set_step_progress("extract", (i + 1) / max(len(files), 1) * 100)

            for file_path in zip_files:
                if not isinstance(file_path, str) or not os.path.isabs(file_path):
                    proc.update_step("extract", "error", error_message=f"Invalid zip file path: {file_path}")
                    continue
                if os.path.exists(file_path):
                    dest = os.path.join(tmp_dir, os.path.basename(file_path))
                    shutil.copy(file_path, dest)
                    file_paths.append(dest)
                else:
                    proc.update_step("extract", "error", error_message=f"Zip file not found: {file_path}")

            proc.complete_step("extract")

            # Step 2: Compress
            pdf_handler = PDFHandler(
                enable_compression=config['enable_compression'],
                quality_preset=config['quality_preset'],
                smallpdf_api_key=config['smallpdf_api_key']
            )

            compression_results = []
            if config['enable_compression'] and pdf_handler.compressor:
                proc.update_step("compress", "running")

                for i, file_path in enumerate(file_paths):
                    comp_result = pdf_handler.compressor.compress(file_path)
                    if comp_result.get('success'):
                        compression_results.append(comp_result)
                        result['original_size'] += comp_result.get('original_size', 0)
                        result['compressed_size'] += comp_result.get('compressed_size', 0)
                    proc.set_step_progress("compress", (i + 1) / len(file_paths) * 100)

            proc.complete_step("compress")

            # Step 3: Number exhibits
            proc.update_step("number", "running")
            numbered_files = []
            exhibit_list = []
            # Initialize AI classifier for labels/analysis (uses Anthropic or OpenAI if available)
            api_key = st.session_state.get('anthropic_api_key')
            classifier = AIClassifier(api_key=api_key)

            for i, file_path in enumerate(file_paths):
                # Get exhibit number
                if config['numbering_style'] == "letters":
                    exhibit_num = chr(65 + i) if i < 26 else f"A{chr(65 + i - 26)}"
                elif config['numbering_style'] == "numbers":
                    exhibit_num = str(i + 1)
                else:
                    exhibit_num = to_roman(i + 1)

                # Track info (initial)
                exhibit_info = {
                    'number': exhibit_num,
                    'title': Path(file_path).stem,
                    'filename': os.path.basename(file_path),
                    'pages': get_pdf_page_count(file_path)
                }

                # Attempt AI-driven short label and content analysis BEFORE creating cover
                short_label = None
                analysis = None
                try:
                    with open(file_path, 'rb') as _f:
                        content_bytes = _f.read()
                    print(f'AI analyzing file: {os.path.basename(file_path)}')
                    print(f'Visa type: {config.get("visa_type")}')
                    short_label = classifier.generate_short_label(content_bytes, exhibit_info['filename'], config['visa_type'])
                    analysis = classifier.analyze_pdf(content_bytes, exhibit_info['filename'], config['visa_type'])
                    if short_label:
                        exhibit_info['title'] = short_label
                    if analysis:
                        exhibit_info['analysis'] = analysis
                        # copy common fields for easy access
                        exhibit_info['summary'] = analysis.get('summary')
                        exhibit_info['document_type'] = analysis.get('document_type')
                        exhibit_info['dates'] = analysis.get('dates')
                        exhibit_info['forms'] = analysis.get('forms')
                        exhibit_info['visa_mentions'] = analysis.get('visa_mentions')
                        exhibit_info['entities'] = analysis.get('entities')
                except Exception as e:
                    print(f"AI analysis error for {file_path}: {e}")

                # Add exhibit number with cover page, including title/summary when available
                try:
                    # Provide extracted text and bytes so the PDF handler can append full text and images
                    # Only extract and attach full text/images if user enabled the option
                    if config.get('include_full_text_images'):
                        try:
                            extracted_text = classifier.extract_text_from_pdf(content_bytes, max_chars=200000)
                        except Exception:
                            extracted_text = None
                    else:
                        extracted_text = None

                    numbered_file = pdf_handler.add_exhibit_number_with_cover(
                        file_path,
                        exhibit_num,
                        title=exhibit_info.get('title'),
                        summary=exhibit_info.get('summary'),
                        extracted_text=extracted_text,
                        content_bytes=content_bytes if config.get('include_full_text_images') else None
                    )
                except Exception as e:
                    print(f"Error creating numbered file for {file_path}: {e}")
                    numbered_file = pdf_handler.add_exhibit_number_with_cover(file_path, exhibit_num)

                numbered_files.append(numbered_file)

                if i < len(compression_results):
                    exhibit_info['compression'] = {
                        'reduction': compression_results[i].get('reduction_percent', 0),
                        'method': compression_results[i].get('method', 'none')
                    }

                exhibit_list.append(exhibit_info)
                result['total_pages'] += exhibit_info['pages']

                proc.set_step_progress("number", (i + 1) / len(file_paths) * 100)

            proc.complete_step("number")

            # Step 4: Generate TOC
            if config['add_toc']:
                proc.update_step("toc", "running")
                toc_file = pdf_handler.generate_table_of_contents(
                    exhibit_list,
                    config['visa_type'],
                    os.path.join(tmp_dir, "TOC.pdf")
                )
                numbered_files.insert(0, toc_file)
            proc.complete_step("toc")

            # Step 5: Generate Cover Letter
            if config['add_cover_letter']:
                proc.update_step("cover", "running")
                try:
                    # Get case context
                    case_context = get_case_context()
                    
                    # Debug: Print case context
                    print(f"Case context: {case_context}")
                    
                    # Create template engine
                    engine = DOCXTemplateEngine()
                    
                    # Prepare case data
                    case_data = {
                        'visa_type': config['visa_type'],
                        'beneficiary_name': getattr(case_context, 'beneficiary_name', None) or 'Beneficiary',
                        'petitioner_name': getattr(case_context, 'petitioner_name', None) or 'Petitioner',
                        'service_center': getattr(case_context, 'service_center', None) or 'California Service Center',
                        'nationality': getattr(case_context, 'nationality', None) or '',
                        'job_title': getattr(case_context, 'job_title', None) or '',
                        'field': getattr(case_context, 'field', None) or '',
                        'duration': getattr(case_context, 'duration', None) or '3 years',
                        'processing_type': getattr(case_context, 'processing_type', None) or 'Regular',
                        'filing_fee': getattr(case_context, 'filing_fee', None) or '$460',
                        'premium_fee': getattr(case_context, 'premium_fee', None) or '$2,805'
                    }
                    
                    # Debug: Print case data
                    print(f"Case data: {case_data}")
                    
                    # Generate cover letter
                    cover_letter_path = os.path.join(tmp_dir, "Cover_Letter.docx")
                    from templates.docx_engine import CaseData
                    case_obj = CaseData(**case_data)
                    engine.generate_cover_letter(case_obj, exhibit_list, cover_letter_path)
                    result['cover_letter_path'] = cover_letter_path
                    
                    print(f"Cover letter generated: {cover_letter_path}")
                    
                except Exception as e:
                    print(f"Error generating cover letter: {e}")
                    import traceback
                    traceback.print_exc()
                    result['cover_letter_path'] = None
            proc.complete_step("cover")

            # Step 5a: Generate Filing Instructions (DIY) if requested
            proc.update_step("filing_instructions", "running")
            if config.get('add_filing_instructions'):
                try:
                    # Create template engine
                    engine = DOCXTemplateEngine()

                    # Reuse case context
                    case_context = get_case_context()
                    case_data = {
                        'visa_type': config['visa_type'],
                        'beneficiary_name': getattr(case_context, 'beneficiary_name', None) or 'Beneficiary',
                        'petitioner_name': getattr(case_context, 'petitioner_name', None) or 'Petitioner',
                        'service_center': getattr(case_context, 'service_center', None) or 'California Service Center',
                        'nationality': getattr(case_context, 'nationality', None) or '',
                        'job_title': getattr(case_context, 'job_title', None) or '',
                        'field': getattr(case_context, 'field', None) or '',
                        'duration': getattr(case_context, 'duration', None) or '3 years',
                        'processing_type': getattr(case_context, 'processing_type', None) or 'Regular',
                        'filing_fee': getattr(case_context, 'filing_fee', None) or '$460',
                        'premium_fee': getattr(case_context, 'premium_fee', None) or '$2,805',
                        'criteria_met': getattr(case_context, 'criteria_met', []) or []
                    }

                    from templates.docx_engine import CaseData
                    case_obj = CaseData(**case_data)
                    filing_path = os.path.join(tmp_dir, "Filing_Instructions.docx")
                    engine.generate_filing_instructions(case_obj, exhibit_list, filing_path)
                    result['filing_instructions_path'] = filing_path

                except Exception as e:
                    print(f"Error generating filing instructions: {e}")
                    import traceback
                    traceback.print_exc()
                    result['filing_instructions_path'] = None
            proc.complete_step("filing_instructions")

            # Step 5: Merge / select output file
            proc.update_step("merge", "running")

            if numbered_files:
                if config['merge_pdfs']:
                    # Standard behavior: merge all numbered PDFs into one package
                    output_file = os.path.join(tmp_dir, "final_package.pdf")
                    merged_file = pdf_handler.merge_pdfs(numbered_files, output_file)

                    # Copy to persistent location
                    final_output = os.path.join(
                        tempfile.gettempdir(),
                        f"exhibit_package_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
                    )
                    shutil.copy(merged_file, final_output)
                    result['output_file'] = final_output
                else:
                    # If user chose not to merge, still provide a single downloadable file
                    # by exposing the first numbered exhibit as the package output.
                    first_file = numbered_files[0]
                    final_output = os.path.join(
                        tempfile.gettempdir(),
                        f"exhibit_package_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
                    )
                    shutil.copy(first_file, final_output)
                    result['output_file'] = final_output
            else:
                # No numbered files were produced; leave output_file as None
                result['output_file'] = None

            proc.complete_step("merge")

            # Step 6: Finalize
            proc.update_step("finalize", "running")

            # Save results to session state
            st.session_state.exhibit_list = exhibit_list
            
            if compression_results:
                avg_reduction = (
                    (1 - result['compressed_size'] / max(result['original_size'], 1)) * 100
                    if result['original_size'] > 0 else 0
                )
                st.session_state.compression_stats = {
                    'original_size': result['original_size'],
                    'compressed_size': result['compressed_size'],
                    'avg_reduction': avg_reduction,
                    'method': compression_results[0].get('method', 'unknown'),
                    'quality': config['quality_preset']
                }

            st.session_state.exhibits_generated = True
            proc.complete_step("finalize")
            return result

        except Exception as e:
            raise RuntimeError(f"Failed to process exhibits: {e}") from e

    processor.start_processing(process_func)


def get_pdf_page_count(pdf_path: str) -> int:
    """Get number of pages in PDF"""
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(pdf_path)
        return len(reader.pages)
    except:
        return 0


def to_roman(num: int) -> str:
    """Convert number to Roman numeral"""
    val = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1]
    syms = ['M', 'CM', 'D', 'CD', 'C', 'XC', 'L', 'XL', 'X', 'IX', 'V', 'IV', 'I']
    roman_num = ''
    i = 0
    while num > 0:
        for _ in range(num // val[i]):
            roman_num += syms[i]
            num -= val[i]
        i += 1
    return roman_num


def main():
    """Main application entry point"""
    init_session_state()

    # Hidden input for JS-to-Python communication
    # We use on_change to ensure the command is processed before the rest of the script
    st.text_input(
        "internal_action_bridge", 
        key="action_command", 
        label_visibility="collapsed",
        on_change=process_bridge_command,
        placeholder="bridge_connector_v2"
    )
    st.markdown(
        """
        <style>
        /* Robust hiding that keeps element interactive */
        div[data-testid="stTextInput"]:has(input[placeholder="bridge_connector_v2"]) {
            opacity: 0;
            height: 1px;
            overflow: hidden;
            position: absolute;
            z-index: -1;
        }
        /* Fallback */
        input[placeholder="bridge_connector_v2"] {
            opacity: 0;
        }
        </style>
        """, 
        unsafe_allow_html=True
    )

    # Header
    st.markdown('<div class="main-header">📄 Visa Exhibit Generator <span class="version-badge">V2.0</span></div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Professional exhibit packages with AI-powered classification</div>', unsafe_allow_html=True)

    # Sidebar config
    config = render_sidebar()

    # Stage Navigator
    navigator = StageNavigator()

    # Render stage header
    render_stage_header(navigator)

    # Render current stage
    current_stage = navigator.current_stage

    if current_stage == 0:
        render_stage_1_context(navigator)
    elif current_stage == 1:
        render_stage_2_upload(navigator, config)
    elif current_stage == 2:
        render_stage_3_classify(navigator, config)
    elif current_stage == 3:
        render_stage_4_review(navigator, config)
    elif current_stage == 4:
        render_stage_5_generate(navigator, config)
    elif current_stage == 5:
        render_stage_6_complete(navigator, config)


if __name__ == "__main__":
    main()
