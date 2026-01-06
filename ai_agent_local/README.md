# AI Agent for PDF Analysis

This is a Python Flask application that processes PDF files and generates AI-powered summaries and keywords.

## Features
- Extracts text from PDF files using PyMuPDF
- Generates AI summaries and keywords using Dashscope API
- Provides a REST API for PDF analysis
- Supports both HTTP and HTTPS connections

## Setup

1. Install dependencies:
```bash
pip install flask fitz openai python-dotenv
```

2. Create a `.env` file with your API key:
```env
DASHSCOPE_API_KEY=your_api_key_here
SSL_CERT_FILE=path/to/your/certificate.crt  # Optional, for HTTPS
SSL_KEY_FILE=path/to/your/private.key      # Optional, for HTTPS
```

## Usage

Run the server:
```bash
python agent.py
```

The server will start on `http://127.0.0.1:3333` by default. If SSL certificates are provided in the environment variables, it will run with HTTPS support.

## API Endpoints

- `GET /ping` - Check if the server is running
- `POST /process` - Process a PDF file and generate summary/keywords

## HTTPS Configuration

For production use, it's recommended to run the server with HTTPS. To enable HTTPS:

1. Obtain SSL certificates for your server
2. Set the `SSL_CERT_FILE` and `SSL_KEY_FILE` environment variables in your `.env` file
3. The server will automatically use HTTPS when these are provided
