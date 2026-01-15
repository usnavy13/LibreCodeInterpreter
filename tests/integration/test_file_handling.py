"""Tests for file handling with container pooling.

These tests verify that generated files are correctly retrieved from containers
when container pooling is enabled.
"""

import pytest
import aiohttp
import ssl
import os

# Test configuration
API_URL = os.getenv("TEST_API_URL", "https://localhost")
API_KEY = os.getenv("TEST_API_KEY", "test-api-key-for-development-only")


@pytest.fixture
def ssl_context():
    """Create SSL context that doesn't verify certificates for local testing."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


@pytest.fixture
def headers():
    """API headers."""
    return {"X-API-Key": API_KEY, "Content-Type": "application/json"}


class TestFileGeneration:
    """Test file generation and retrieval."""

    @pytest.mark.asyncio
    async def test_generated_image_is_valid_png(self, ssl_context, headers):
        """Test that generated PNG files are correctly retrieved with full content."""
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            # Generate a matplotlib image
            payload = {
                "lang": "py",
                "code": """
import matplotlib.pyplot as plt
plt.figure(figsize=(6, 4))
plt.plot([1, 2, 3, 4], [1, 4, 9, 16], 'ro-')
plt.title('Test Chart')
plt.savefig('/mnt/data/test_chart.png', dpi=100)
print('Chart saved')
""",
                "entity_id": "test-file-gen-png"
            }

            async with session.post(
                f"{API_URL}/exec", json=payload, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                result = await resp.json()

                # Verify file was detected
                files = result.get("files", [])
                assert len(files) >= 1, "Expected at least one generated file"

                file_info = files[0]
                assert file_info.get("name") == "test_chart.png"
                assert file_info.get("id") is not None

                session_id = result.get("session_id")
                file_id = file_info.get("id")

                # Download the file
                download_url = f"{API_URL}/download/{session_id}/{file_id}"
                async with session.get(
                    download_url, headers=headers, ssl=ssl_context
                ) as dl_resp:
                    assert dl_resp.status == 200
                    content = await dl_resp.read()

                    # Verify it's a valid PNG (minimum reasonable size)
                    assert len(content) > 1000, f"File too small: {len(content)} bytes"

                    # Check PNG magic bytes
                    assert content[:8] == b'\x89PNG\r\n\x1a\n', "Not a valid PNG file"

    @pytest.mark.asyncio
    async def test_multiple_generated_files(self, ssl_context, headers):
        """Test that multiple generated files are all correctly retrieved."""
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            payload = {
                "lang": "py",
                "code": """
import matplotlib.pyplot as plt
import numpy as np

# Create 3 different plots
for name in ['alpha', 'beta', 'gamma']:
    plt.figure()
    plt.plot(np.random.randn(10))
    plt.title(f'{name} plot')
    plt.savefig(f'/mnt/data/{name}.png')
    print(f'Created {name}.png')
""",
                "entity_id": "test-multi-files"
            }

            async with session.post(
                f"{API_URL}/exec", json=payload, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                result = await resp.json()

                files = result.get("files", [])
                assert len(files) >= 3, f"Expected 3 files, got {len(files)}"

                session_id = result.get("session_id")
                filenames = {f.get("name") for f in files}

                # Verify all expected files are present
                assert "alpha.png" in filenames
                assert "beta.png" in filenames
                assert "gamma.png" in filenames

                # Download each file and verify
                for file_info in files:
                    download_url = f"{API_URL}/download/{session_id}/{file_info['id']}"
                    async with session.get(
                        download_url, headers=headers, ssl=ssl_context
                    ) as dl_resp:
                        assert dl_resp.status == 200
                        content = await dl_resp.read()

                        assert len(content) > 1000, (
                            f"File {file_info['name']} too small: {len(content)} bytes"
                        )
                        assert content[:4] == b'\x89PNG', (
                            f"File {file_info['name']} is not a valid PNG"
                        )

    @pytest.mark.asyncio
    async def test_text_file_generation(self, ssl_context, headers):
        """Test that text files are correctly generated and retrieved."""
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            payload = {
                "lang": "py",
                "code": """
# Write a text file
with open('/mnt/data/output.txt', 'w') as f:
    f.write('Hello, World!\\n')
    f.write('This is a test file.\\n')
print('Text file created')
""",
                "entity_id": "test-text-file"
            }

            async with session.post(
                f"{API_URL}/exec", json=payload, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                result = await resp.json()

                files = result.get("files", [])
                assert len(files) >= 1, "Expected at least one generated file"

                # Find the text file
                txt_file = next(
                    (f for f in files if f.get("name") == "output.txt"), None
                )
                assert txt_file is not None, "output.txt not found in generated files"

                session_id = result.get("session_id")

                # Download and verify content
                download_url = f"{API_URL}/download/{session_id}/{txt_file['id']}"
                async with session.get(
                    download_url, headers=headers, ssl=ssl_context
                ) as dl_resp:
                    assert dl_resp.status == 200
                    content = await dl_resp.text()

                    assert "Hello, World!" in content
                    assert "This is a test file." in content

    @pytest.mark.asyncio
    async def test_csv_file_generation(self, ssl_context, headers):
        """Test that CSV files are correctly generated and retrieved."""
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            payload = {
                "lang": "py",
                "code": """
import pandas as pd

df = pd.DataFrame({
    'name': ['Alice', 'Bob', 'Charlie'],
    'age': [25, 30, 35],
    'city': ['NYC', 'LA', 'Chicago']
})
df.to_csv('/mnt/data/people.csv', index=False)
print(f'Created CSV with {len(df)} rows')
""",
                "entity_id": "test-csv-file"
            }

            async with session.post(
                f"{API_URL}/exec", json=payload, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                result = await resp.json()

                files = result.get("files", [])
                csv_file = next(
                    (f for f in files if f.get("name") == "people.csv"), None
                )
                assert csv_file is not None, "people.csv not found"

                session_id = result.get("session_id")

                # Download and verify
                download_url = f"{API_URL}/download/{session_id}/{csv_file['id']}"
                async with session.get(
                    download_url, headers=headers, ssl=ssl_context
                ) as dl_resp:
                    assert dl_resp.status == 200
                    content = await dl_resp.text()

                    assert "name,age,city" in content
                    assert "Alice" in content
                    assert "Bob" in content
                    assert "Charlie" in content


class TestFileHandlingWithPooling:
    """Test file handling specifically with container pooling enabled."""

    @pytest.mark.asyncio
    async def test_file_generation_after_pool_acquisition(self, ssl_context, headers):
        """Test that files are correctly retrieved when container comes from pool."""
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            # Use unique entity_id to get a fresh session/container from pool
            import time
            entity_id = f"test-pool-file-{int(time.time())}"

            payload = {
                "lang": "py",
                "code": """
import matplotlib.pyplot as plt
plt.figure()
plt.pie([30, 40, 30], labels=['A', 'B', 'C'])
plt.savefig('/mnt/data/pie.png')
print('Pie chart created')
""",
                "entity_id": entity_id
            }

            async with session.post(
                f"{API_URL}/exec", json=payload, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                result = await resp.json()

                files = result.get("files", [])
                assert len(files) >= 1, "No files generated"

                pie_file = next((f for f in files if "pie" in f.get("name", "")), None)
                assert pie_file is not None, "pie.png not found"

                session_id = result.get("session_id")

                # Download and verify it's a real PNG
                download_url = f"{API_URL}/download/{session_id}/{pie_file['id']}"
                async with session.get(
                    download_url, headers=headers, ssl=ssl_context
                ) as dl_resp:
                    assert dl_resp.status == 200
                    content = await dl_resp.read()

                    # Should be a substantial PNG file, not a stub
                    assert len(content) > 5000, (
                        f"File appears truncated: {len(content)} bytes"
                    )
                    assert content[:8] == b'\x89PNG\r\n\x1a\n', "Invalid PNG"

    @pytest.mark.asyncio
    async def test_large_file_generation(self, ssl_context, headers):
        """Test that large generated files are correctly retrieved."""
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            payload = {
                "lang": "py",
                "code": """
import matplotlib.pyplot as plt
import numpy as np

# Create a large, detailed plot
fig, axes = plt.subplots(2, 2, figsize=(12, 10), dpi=150)

for ax in axes.flat:
    x = np.linspace(0, 10, 1000)
    for i in range(10):
        ax.plot(x, np.sin(x + i * 0.5) + np.random.randn(1000) * 0.1)

plt.tight_layout()
plt.savefig('/mnt/data/large_plot.png')
print('Large plot created')
""",
                "entity_id": "test-large-file"
            }

            async with session.post(
                f"{API_URL}/exec", json=payload, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                result = await resp.json()

                files = result.get("files", [])
                large_file = next(
                    (f for f in files if f.get("name") == "large_plot.png"), None
                )
                assert large_file is not None, "large_plot.png not found"

                session_id = result.get("session_id")

                # Download and verify
                download_url = f"{API_URL}/download/{session_id}/{large_file['id']}"
                async with session.get(
                    download_url, headers=headers, ssl=ssl_context
                ) as dl_resp:
                    assert dl_resp.status == 200
                    content = await dl_resp.read()

                    # Large detailed plot should be > 50KB
                    assert len(content) > 50000, (
                        f"Large file too small: {len(content)} bytes"
                    )
                    assert content[:8] == b'\x89PNG\r\n\x1a\n', "Invalid PNG"


class TestUploadAnalyzeDownload:
    """Test complete workflow: upload file → analyze with pandas → download results."""

    @pytest.mark.asyncio
    async def test_upload_csv_analyze_download_results(self, ssl_context, headers):
        """Test uploading a CSV, performing pandas analysis, and downloading the results."""
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            import time
            entity_id = f"test-upload-analyze-{int(time.time())}"

            # Step 1: Upload a CSV file
            csv_content = "product,quantity,price\nWidget A,100,9.99\nWidget B,250,14.99\nWidget C,75,24.99\nWidget D,300,4.99\nWidget E,150,19.99"

            form_data = aiohttp.FormData()
            form_data.add_field('files', csv_content.encode(),
                                filename='sales_data.csv',
                                content_type='text/csv')
            form_data.add_field('entity_id', entity_id)

            upload_headers = {"X-API-Key": API_KEY}

            async with session.post(
                f"{API_URL}/upload",
                data=form_data,
                headers=upload_headers,
                ssl=ssl_context
            ) as resp:
                assert resp.status == 200, f"Upload failed: {await resp.text()}"
                upload_result = await resp.json()

                session_id = upload_result.get("session_id")
                uploaded_files = upload_result.get("files", [])
                assert len(uploaded_files) >= 1, "No files in upload response"

                uploaded_file = uploaded_files[0]
                file_id = uploaded_file.get("id") or uploaded_file.get("fileId")
                assert file_id is not None, "No file ID returned"

            # Step 2: Execute analysis code that reads the uploaded file and creates a report
            from textwrap import dedent
            analysis_code = dedent("""
                import pandas as pd

                # Read the uploaded CSV (files are placed in /mnt/data/)
                df = pd.read_csv('/mnt/data/sales_data.csv')

                # Perform analysis
                df['total_value'] = df['quantity'] * df['price']
                summary = df.describe()

                # Create a summary report
                report = f'''Sales Analysis Report
                =====================
                Total Products: {len(df)}
                Total Revenue: ${df["total_value"].sum():.2f}
                Average Price: ${df["price"].mean():.2f}
                Top Product by Quantity: {df.loc[df["quantity"].idxmax(), "product"]}
                Top Product by Value: {df.loc[df["total_value"].idxmax(), "product"]}
                '''

                # Save the analysis results
                df.to_csv('/mnt/data/analyzed_sales.csv', index=False)

                with open('/mnt/data/sales_report.txt', 'w') as f:
                    f.write(report)

                print(report)
            """).strip()

            exec_payload = {
                "lang": "py",
                "code": analysis_code,
                "entity_id": entity_id,
                "files": [{
                    "id": file_id,
                    "session_id": session_id,
                    "name": "sales_data.csv"
                }]
            }

            async with session.post(
                f"{API_URL}/exec",
                json=exec_payload,
                headers=headers,
                ssl=ssl_context
            ) as resp:
                assert resp.status == 200, f"Exec failed: {await resp.text()}"
                exec_result = await resp.json()

                # Verify execution succeeded
                stdout = exec_result.get("stdout", "")
                stderr = exec_result.get("stderr", "")
                assert "Sales Analysis Report" in stdout, f"Analysis failed. stdout: {stdout}, stderr: {stderr}"

                # Find generated files
                files = exec_result.get("files", [])
                assert len(files) >= 2, f"Expected 2 output files, got {len(files)}"

                csv_output = next((f for f in files if "analyzed_sales.csv" in f.get("name", "")), None)
                txt_output = next((f for f in files if "sales_report.txt" in f.get("name", "")), None)

                assert csv_output is not None, "analyzed_sales.csv not found in output"
                assert txt_output is not None, "sales_report.txt not found in output"

                # Use session_id from exec result for downloading generated files
                exec_session_id = exec_result.get("session_id")

            # Step 3: Download and verify the analyzed CSV
            download_url = f"{API_URL}/download/{exec_session_id}/{csv_output['id']}"
            async with session.get(download_url, headers=upload_headers, ssl=ssl_context) as resp:
                assert resp.status == 200, f"CSV download failed: {resp.status}"
                csv_result = await resp.text()

                # Verify the analysis added the total_value column
                assert "total_value" in csv_result, "Analysis column not found in output CSV"
                assert "Widget A" in csv_result
                # Widget A: 100 * 9.99 = 999.0
                assert "999" in csv_result, "Calculated total_value not found"

            # Step 4: Download and verify the text report
            download_url = f"{API_URL}/download/{exec_session_id}/{txt_output['id']}"
            async with session.get(download_url, headers=upload_headers, ssl=ssl_context) as resp:
                assert resp.status == 200, f"Report download failed: {resp.status}"
                report_content = await resp.text()

                assert "Sales Analysis Report" in report_content
                assert "Total Products: 5" in report_content
                assert "Total Revenue:" in report_content
                assert "Top Product by Quantity:" in report_content

    @pytest.mark.asyncio
    async def test_upload_image_process_download(self, ssl_context, headers):
        """Test uploading an image, processing with OpenCV, and downloading the result."""
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            import time
            entity_id = f"test-image-process-{int(time.time())}"

            # Step 1: Create and upload a simple PNG image (100x100 red square)
            # PNG header for a minimal valid image is complex, so we'll generate one with code
            # First, execute code to create a test image, then use it

            # Create a test image via execution first
            create_image_code = """
import cv2
import numpy as np

# Create a simple test image (100x100 with colored squares)
img = np.zeros((100, 100, 3), dtype=np.uint8)
img[0:50, 0:50] = [255, 0, 0]    # Blue (BGR)
img[0:50, 50:100] = [0, 255, 0]  # Green
img[50:100, 0:50] = [0, 0, 255]  # Red
img[50:100, 50:100] = [255, 255, 0]  # Cyan

cv2.imwrite('/mnt/data/test_input.png', img)
print(f'Created test image: {img.shape}')
"""

            async with session.post(
                f"{API_URL}/exec",
                json={"lang": "py", "code": create_image_code, "entity_id": entity_id},
                headers=headers,
                ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                result = await resp.json()
                session_id = result.get("session_id")

                # Get the created image file
                files = result.get("files", [])
                input_image = next((f for f in files if "test_input.png" in f.get("name", "")), None)
                assert input_image is not None, "Test image not created"

            # Step 2: Process the image (apply blur and edge detection)
            process_code = """
import cv2
import numpy as np

# Read the input image (files are placed in /mnt/data/)
img = cv2.imread('/mnt/data/test_input.png')
print(f'Input shape: {img.shape}')

# Apply Gaussian blur
blurred = cv2.GaussianBlur(img, (5, 5), 0)

# Apply Canny edge detection
gray = cv2.cvtColor(blurred, cv2.COLOR_BGR2GRAY)
edges = cv2.Canny(gray, 50, 150)

# Save results
cv2.imwrite('/mnt/data/blurred.png', blurred)
cv2.imwrite('/mnt/data/edges.png', edges)

print(f'Processed images saved. Edges shape: {edges.shape}')
"""

            exec_payload = {
                "lang": "py",
                "code": process_code,
                "entity_id": entity_id,
                "files": [{
                    "id": input_image['id'],
                    "session_id": session_id,
                    "name": "test_input.png"
                }]
            }

            async with session.post(
                f"{API_URL}/exec",
                json=exec_payload,
                headers=headers,
                ssl=ssl_context
            ) as resp:
                assert resp.status == 200, f"Processing failed: {await resp.text()}"
                result = await resp.json()

                stdout = result.get("stdout", "")
                stderr = result.get("stderr", "")
                assert "Processed images saved" in stdout, f"Processing failed. stderr: {stderr}"

                files = result.get("files", [])
                blurred_file = next((f for f in files if "blurred.png" in f.get("name", "")), None)
                edges_file = next((f for f in files if "edges.png" in f.get("name", "")), None)

                assert blurred_file is not None, "blurred.png not found"
                assert edges_file is not None, "edges.png not found"

            # Step 3: Download and verify the processed images
            upload_headers = {"X-API-Key": API_KEY}

            # Download blurred image
            download_url = f"{API_URL}/download/{session_id}/{blurred_file['id']}"
            async with session.get(download_url, headers=upload_headers, ssl=ssl_context) as resp:
                assert resp.status == 200
                content = await resp.read()
                assert len(content) > 100, f"Blurred image too small: {len(content)}"
                assert content[:4] == b'\x89PNG', "Blurred output is not a valid PNG"

            # Download edges image
            download_url = f"{API_URL}/download/{session_id}/{edges_file['id']}"
            async with session.get(download_url, headers=upload_headers, ssl=ssl_context) as resp:
                assert resp.status == 200
                content = await resp.read()
                assert len(content) > 100, f"Edges image too small: {len(content)}"
                assert content[:4] == b'\x89PNG', "Edges output is not a valid PNG"

    @pytest.mark.asyncio
    async def test_upload_json_transform_download(self, ssl_context, headers):
        """Test uploading JSON data, transforming it, and downloading the result."""
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            import time
            import json
            entity_id = f"test-json-transform-{int(time.time())}"

            # Step 1: Upload JSON data
            json_data = {
                "users": [
                    {"name": "Alice", "age": 30, "department": "Engineering"},
                    {"name": "Bob", "age": 25, "department": "Marketing"},
                    {"name": "Charlie", "age": 35, "department": "Engineering"},
                    {"name": "Diana", "age": 28, "department": "Sales"},
                    {"name": "Eve", "age": 32, "department": "Engineering"}
                ]
            }

            form_data = aiohttp.FormData()
            form_data.add_field('files', json.dumps(json_data).encode(),
                                filename='users.json',
                                content_type='application/json')
            form_data.add_field('entity_id', entity_id)

            upload_headers = {"X-API-Key": API_KEY}

            async with session.post(
                f"{API_URL}/upload",
                data=form_data,
                headers=upload_headers,
                ssl=ssl_context
            ) as resp:
                assert resp.status == 200, f"Upload failed: {await resp.text()}"
                upload_result = await resp.json()
                session_id = upload_result.get("session_id")
                uploaded_file = upload_result.get("files", [])[0]
                file_id = uploaded_file.get("id") or uploaded_file.get("fileId")

            # Step 2: Transform the data
            from textwrap import dedent
            transform_code = dedent("""
                import json
                import pandas as pd

                # Read uploaded JSON (files are placed in /mnt/data/)
                with open('/mnt/data/users.json') as f:
                    data = json.load(f)

                # Convert to DataFrame and analyze
                df = pd.DataFrame(data['users'])

                # Group by department
                dept_summary = df.groupby('department').agg({
                    'name': 'count',
                    'age': 'mean'
                }).rename(columns={'name': 'count', 'age': 'avg_age'})

                # Create output
                output = {
                    'total_users': len(df),
                    'avg_age': df['age'].mean(),
                    'department_breakdown': dept_summary.to_dict('index')
                }

                # Save transformed data
                with open('/mnt/data/analysis.json', 'w') as f:
                    json.dump(output, f, indent=2)

                print(json.dumps(output, indent=2))
            """).strip()

            exec_payload = {
                "lang": "py",
                "code": transform_code,
                "entity_id": entity_id,
                "files": [{
                    "id": file_id,
                    "session_id": session_id,
                    "name": "users.json"
                }]
            }

            async with session.post(
                f"{API_URL}/exec",
                json=exec_payload,
                headers=headers,
                ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                result = await resp.json()

                files = result.get("files", [])
                json_output = next((f for f in files if "analysis.json" in f.get("name", "")), None)
                assert json_output is not None, "analysis.json not found"

                # Use session_id from exec result for downloading
                exec_session_id = result.get("session_id")

            # Step 3: Download and verify the result
            download_url = f"{API_URL}/download/{exec_session_id}/{json_output['id']}"
            async with session.get(download_url, headers=upload_headers, ssl=ssl_context) as resp:
                assert resp.status == 200
                content = await resp.text()

                result_data = json.loads(content)
                assert result_data['total_users'] == 5
                assert 'department_breakdown' in result_data
                assert 'Engineering' in result_data['department_breakdown']
                assert result_data['department_breakdown']['Engineering']['count'] == 3


class TestStatePersistence:
    """Test Python state persistence across executions."""

    @pytest.mark.asyncio
    async def test_variable_persists_across_executions(self, ssl_context, headers):
        """Test that a variable defined in one execution persists to the next.

        State persistence requires passing the session_id from the first execution
        to subsequent executions.
        """
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            # Execution 1: Define a variable
            payload1 = {
                "lang": "py",
                "code": "x = 42\nprint('defined x =', x)"
            }

            async with session.post(
                f"{API_URL}/exec", json=payload1, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                result1 = await resp.json()

                assert "defined x = 42" in result1.get("stdout", "")
                assert result1.get("has_state") is True, "Expected has_state=True after Python execution"
                session_id = result1.get("session_id")
                assert session_id, "No session_id returned"

            # Execution 2: Use the variable (passing session_id to continue state)
            payload2 = {
                "lang": "py",
                "code": "print(f'x from state: {x}')",
                "session_id": session_id
            }

            async with session.post(
                f"{API_URL}/exec", json=payload2, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                result2 = await resp.json()

                # Session ID should be preserved
                assert result2.get("session_id") == session_id, \
                    f"Session ID changed: expected {session_id}, got {result2.get('session_id')}"

                stdout = result2.get("stdout", "")
                assert "x from state: 42" in stdout, \
                    f"Variable not persisted. stdout: {stdout}, stderr: {result2.get('stderr', '')}"

    @pytest.mark.asyncio
    async def test_numpy_array_persists(self, ssl_context, headers):
        """Test that numpy arrays persist across executions."""
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            # Execution 1: Create numpy array
            payload1 = {
                "lang": "py",
                "code": "import numpy as np\narr = np.array([1, 2, 3, 4, 5])\nprint('created array with sum:', arr.sum())"
            }

            async with session.post(
                f"{API_URL}/exec", json=payload1, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                result1 = await resp.json()
                assert "sum: 15" in result1.get("stdout", "")
                session_id = result1.get("session_id")

            # Execution 2: Use the array (passing session_id)
            payload2 = {
                "lang": "py",
                "code": "print(f'array sum from state: {arr.sum()}')",
                "session_id": session_id
            }

            async with session.post(
                f"{API_URL}/exec", json=payload2, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                result2 = await resp.json()

                stdout = result2.get("stdout", "")
                assert "array sum from state: 15" in stdout, f"NumPy array not persisted. stdout: {stdout}"

    @pytest.mark.asyncio
    async def test_pandas_dataframe_persists(self, ssl_context, headers):
        """Test that pandas DataFrames persist across executions."""
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            # Execution 1: Create DataFrame
            payload1 = {
                "lang": "py",
                "code": """import pandas as pd
df = pd.DataFrame({'name': ['Alice', 'Bob', 'Charlie'], 'age': [25, 30, 35]})
print(f'created df with {len(df)} rows')"""
            }

            async with session.post(
                f"{API_URL}/exec", json=payload1, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                result1 = await resp.json()
                assert "3 rows" in result1.get("stdout", "")
                session_id = result1.get("session_id")

            # Execution 2: Use the DataFrame (passing session_id)
            payload2 = {
                "lang": "py",
                "code": "print('names:', df['name'].tolist())",
                "session_id": session_id
            }

            async with session.post(
                f"{API_URL}/exec", json=payload2, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                result2 = await resp.json()

                stdout = result2.get("stdout", "")
                assert "Alice" in stdout and "Bob" in stdout, f"DataFrame not persisted. stdout: {stdout}"

    @pytest.mark.asyncio
    async def test_function_persists(self, ssl_context, headers):
        """Test that user-defined functions persist across executions."""
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            # Execution 1: Define a function
            payload1 = {
                "lang": "py",
                "code": "def greet(name):\n    return f'Hello, {name}!'\nprint('function defined')"
            }

            async with session.post(
                f"{API_URL}/exec", json=payload1, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                result1 = await resp.json()
                assert "function defined" in result1.get("stdout", "")
                session_id = result1.get("session_id")

            # Execution 2: Call the function (passing session_id)
            payload2 = {
                "lang": "py",
                "code": "print(greet('World'))",
                "session_id": session_id
            }

            async with session.post(
                f"{API_URL}/exec", json=payload2, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                result2 = await resp.json()

                stdout = result2.get("stdout", "")
                assert "Hello, World!" in stdout, f"Function not persisted. stdout: {stdout}"

    @pytest.mark.asyncio
    async def test_exec_response_has_state_fields(self, ssl_context, headers):
        """Test that Python execution response includes state fields."""
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            payload = {
                "lang": "py",
                "code": "x = 'hello world'\nprint(x)"
            }

            async with session.post(
                f"{API_URL}/exec", json=payload, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                result = await resp.json()

                # Check state fields are present
                assert "has_state" in result, "Response missing has_state field"
                assert result["has_state"] is True, "Expected has_state=True for Python"
                assert "state_size" in result, "Response missing state_size field"
                assert result["state_size"] is not None, "Expected state_size to be set"
                assert result["state_size"] > 0, "Expected positive state_size"

    @pytest.mark.asyncio
    async def test_session_id_continues_state(self, ssl_context, headers):
        """Test that passing session_id explicitly continues state."""
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            # Execution 1: Define variable, get session_id
            payload1 = {
                "lang": "py",
                "code": "counter = 100\nprint('counter =', counter)"
            }

            async with session.post(
                f"{API_URL}/exec", json=payload1, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                result1 = await resp.json()
                session_id = result1.get("session_id")
                assert session_id, "No session_id in response"
                assert "counter = 100" in result1.get("stdout", "")

            # Execution 2: Use explicit session_id to continue state
            payload2 = {
                "lang": "py",
                "code": "counter += 1\nprint('counter =', counter)",
                "session_id": session_id
            }

            async with session.post(
                f"{API_URL}/exec", json=payload2, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                result2 = await resp.json()

                stdout = result2.get("stdout", "")
                assert "counter = 101" in stdout, f"State not continued with session_id. stdout: {stdout}"
                # Should return same session_id
                assert result2.get("session_id") == session_id, "Session ID should be the same"

    @pytest.mark.asyncio
    async def test_state_info_endpoint(self, ssl_context, headers):
        """Test that /state/{session_id}/info returns correct metadata after execution."""
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            # Create state via execution
            payload = {
                "lang": "py",
                "code": "data = {'key': 'value'}\nprint('created')"
            }

            async with session.post(
                f"{API_URL}/exec", json=payload, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                result = await resp.json()
                session_id = result.get("session_id")
                assert result.get("has_state") is True, "Expected state to be captured"

            # Check state info endpoint
            async with session.get(
                f"{API_URL}/state/{session_id}/info", headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                info = await resp.json()

                assert info.get("exists") is True, f"State should exist. Got: {info}"
                assert info.get("session_id") == session_id
                assert info.get("size_bytes") is not None
                assert info.get("size_bytes") > 0


class TestContainerIsolation:
    """Test that containers are properly isolated between sessions."""

    @pytest.mark.asyncio
    async def test_files_not_visible_across_sessions(self, ssl_context, headers):
        """Test that files created in one session are NOT visible in another session.

        This tests for a container isolation bug where containers are reused
        without proper cleanup, causing files from one session to be visible
        to other sessions.
        """
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            import time
            unique_filename = f"isolation_test_{int(time.time())}.txt"

            # Session 1: Create a unique file
            payload1 = {
                "lang": "py",
                "code": f"with open('/mnt/data/{unique_filename}', 'w') as f:\n    f.write('secret data')\nprint('created')"
            }

            async with session.post(
                f"{API_URL}/exec", json=payload1, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                result1 = await resp.json()
                assert "created" in result1.get("stdout", "")
                session1_id = result1.get("session_id")

            # Session 2: Check if the file is visible (it should NOT be)
            # Do NOT pass session_id to get a fresh session
            payload2 = {
                "lang": "py",
                "code": f"""import os
files = os.listdir('/mnt/data') if os.path.exists('/mnt/data') else []
target_exists = '{unique_filename}' in files
print('files:', files)
print('target_exists:', target_exists)
"""
            }

            async with session.post(
                f"{API_URL}/exec", json=payload2, headers=headers, ssl=ssl_context
            ) as resp:
                assert resp.status == 200
                result2 = await resp.json()
                session2_id = result2.get("session_id")

                # Sessions should be different
                assert session2_id != session1_id, \
                    "Expected different session IDs for independent requests"

                stdout = result2.get("stdout", "")
                # File from session 1 should NOT be visible in session 2
                assert "target_exists: False" in stdout, \
                    f"Container isolation violated! File from session 1 visible in session 2. stdout: {stdout}"

    @pytest.mark.asyncio
    async def test_uploaded_files_mounted_in_container(self, ssl_context, headers):
        """Test that uploaded files are properly mounted in /mnt/data for execution."""
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            import time
            entity_id = f"test-mount-{int(time.time())}"

            # Upload a file
            test_content = "name,value\ntest,123\n"
            form_data = aiohttp.FormData()
            form_data.add_field('files', test_content.encode(),
                                filename='test_upload.csv',
                                content_type='text/csv')

            upload_headers = {"X-API-Key": API_KEY}

            async with session.post(
                f"{API_URL}/upload",
                data=form_data,
                headers=upload_headers,
                ssl=ssl_context
            ) as resp:
                assert resp.status == 200, f"Upload failed: {await resp.text()}"
                upload_result = await resp.json()
                upload_session_id = upload_result.get("session_id")
                uploaded_files = upload_result.get("files", [])
                assert len(uploaded_files) >= 1, "No files in upload response"
                file_id = uploaded_files[0].get("id") or uploaded_files[0].get("fileId")

            # Execute code that reads the uploaded file
            exec_payload = {
                "lang": "py",
                "code": """import os
print('files in /mnt/data:', os.listdir('/mnt/data') if os.path.exists('/mnt/data') else 'N/A')
try:
    with open('/mnt/data/test_upload.csv', 'r') as f:
        content = f.read()
    print('file content:', content)
except FileNotFoundError as e:
    print('ERROR: File not found:', e)
""",
                "files": [{
                    "id": file_id,
                    "session_id": upload_session_id,
                    "name": "test_upload.csv"
                }]
            }

            async with session.post(
                f"{API_URL}/exec",
                json=exec_payload,
                headers=headers,
                ssl=ssl_context
            ) as resp:
                assert resp.status == 200, f"Exec failed: {await resp.text()}"
                result = await resp.json()

                stdout = result.get("stdout", "")
                stderr = result.get("stderr", "")

                # The uploaded file should be in /mnt/data
                assert "ERROR: File not found" not in stdout, \
                    f"Uploaded file not mounted in container. stdout: {stdout}, stderr: {stderr}"
                assert "name,value" in stdout, \
                    f"File content not readable. stdout: {stdout}"
