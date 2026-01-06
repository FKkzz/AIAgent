// Test script to verify the agent.py integration
const https = require('https');
const fs = require('fs');
const path = require('path');

// Test function to call the agent.py endpoint
async function testAgentEndpoint() {
  console.log('Testing agent endpoint...');
  
  // Read a sample PDF file (or create a mock one for testing)
  const samplePdfPath = path.join(__dirname, 'sample.pdf');
  
  // Check if sample PDF exists, if not, we'll just test the endpoint
  if (!fs.existsSync(samplePdfPath)) {
    console.log('Sample PDF not found, testing endpoint availability only...');
  }
  
  // Test the endpoint with a simple request
  const postData = JSON.stringify({
    pdf_path: samplePdfPath,
    action: 'summary'
  });

  const options = {
    hostname: '127.0.0.1',
    port: 3333,
    path: '/analyze',
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Content-Length': postData.length
    }
  };

  return new Promise((resolve, reject) => {
    const req = https.request(options, (res) => {
      console.log(`STATUS: ${res.statusCode}`);
      console.log(`HEADERS: ${JSON.stringify(res.headers)}`);
      
      res.on('data', (chunk) => {
        console.log(`BODY: ${chunk}`);
      });
      
      res.on('end', () => {
        console.log('No more data in response.');
        resolve(true);
      });
    });

    req.on('error', (e) => {
      console.error(`problem with request: ${e.message}`);
      reject(e);
    });

    // Write data to request body
    req.write(postData);
    req.end();
  });
}

// Run the test
testAgentEndpoint()
  .then(() => console.log('Test completed'))
  .catch(err => console.error('Test failed:', err));
