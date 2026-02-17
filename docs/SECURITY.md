# Security Documentation

## Overview

The Code Interpreter API implements multiple layers of security to ensure safe code execution and protect against common web application vulnerabilities.

## Authentication

### API Key Authentication

All API endpoints (except health checks and documentation) require authentication using an API key.

#### Providing API Key

The API key is provided via the `x-api-key` header:

```bash
curl -H "x-api-key: your-api-key" https://api.example.com/sessions
```

#### Configuration

Set the API key in your environment:

```bash
export API_KEY="your-secure-api-key-here"
```

Or in your `.env` file:

```
API_KEY=your-secure-api-key-here
```

**Important**: Use a strong, randomly generated API key in production.

### Rate Limiting

The API implements rate limiting to prevent abuse:

- **Authentication failures**: Max 10 failed attempts per IP per hour
- **API key validation**: Results are cached for 5 minutes to improve performance
- **Request rate limiting**: Additional rate limiting can be configured per endpoint

## Security Middleware

### Security Headers

All responses include security headers:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `X-XSS-Protection: 1; mode=block`
- `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- `Content-Security-Policy: default-src 'self'`
- `Referrer-Policy: strict-origin-when-cross-origin`

### Request Validation

- **Content-Type validation**: Only allowed content types are accepted
- **Request size limits**: Configurable maximum request size
- **Input sanitization**: All user inputs are validated and sanitized

### File Security

#### Filename Validation

Uploaded files are validated for:

- **Path traversal prevention**: `../` and `\` characters are blocked
- **Null byte injection**: Null bytes in filenames are rejected
- **File extension whitelist**: Only allowed file extensions are accepted
- **Filename length limits**: Maximum 255 characters
- **Suspicious characters**: Special characters that could be dangerous are blocked

#### Allowed File Extensions

```
.txt, .csv, .json, .xml, .yaml, .yml,
.py, .js, .ts, .go, .java, .c, .cpp, .h, .hpp,
.rs, .php, .rb, .r, .f90, .d,
.md, .rst, .html, .css,
.png, .jpg, .jpeg, .gif, .svg,
.pdf, .doc, .docx, .xls, .xlsx
```

### Code Execution Security

#### Code Validation

Code is analyzed for potentially dangerous patterns:

- **System imports**: `import os`, `import subprocess`, etc.
- **Dangerous functions**: `eval()`, `exec()`, `__import__()`, etc.
- **File operations**: `open()`, `file()`, etc.
- **Input functions**: `input()`, `raw_input()`, etc.

**Note**: Dangerous patterns generate warnings but don't block execution, as the code runs in isolated nsjail sandboxes.

#### nsjail Sandbox Isolation

- **nsjail sandboxes**: All code runs in isolated nsjail sandboxes with namespace separation
- **PID namespace**: Each sandbox has its own PID 1; processes cannot see or signal other sandboxes
- **Mount namespace**: Minimal filesystem with read-only bind mounts for language runtimes
- **Network namespace**: No network access by default
- **Seccomp filtering**: Restricts available system calls
- **Cgroup limits**: Memory, CPU, and PID limits enforced
- **rlimits**: File size, open files, and stack size restricted
- **Non-root execution**: Code runs as uid 1001 (codeuser)

**Note**: The API container requires `SYS_ADMIN` capability for nsjail to create namespaces and cgroups. No Docker socket is mounted.

### Network Isolation

By default, nsjail sandboxes have no network access. Each sandbox runs in its own network namespace with no connectivity.

### State Persistence Security

Python state persistence introduces additional security considerations:

#### Serialization Security

- **Serialization inside sandboxes**: State is serialized within the isolated nsjail sandbox, not on the host. The host never unpickles user data.
- **cloudpickle usage**: We use cloudpickle for serialization. While pickle-based formats can execute code during deserialization, this only occurs inside the sandboxed nsjail environment.
- **Compression**: State is compressed with lz4 before storage, providing minor obfuscation and reducing attack surface.
- **Base64 encoding**: Final storage uses base64 encoding for safe transport.

#### Storage Security

- **Redis encryption**: Consider enabling Redis TLS in production for encrypted state storage
- **MinIO encryption**: Enable server-side encryption for archived states
- **TTL-based cleanup**: States automatically expire (2 hours in Redis, 7 days in MinIO archives)
- **Size limits**: `STATE_MAX_SIZE_MB` prevents denial-of-service via large states

#### Session Isolation

- **Session binding**: State is bound to `session_id`, not directly accessible by other sessions
- **User scoping**: Sessions are scoped by `user_id` and `entity_id`
- **No cross-session access**: One user's session cannot access another user's state

#### Disabling State Persistence

If state persistence poses unacceptable risk for your use case:

```bash
STATE_PERSISTENCE_ENABLED=false
```

This ensures each execution starts with a clean namespace.

#### Audit Events

State persistence operations are logged:

- State save (size, session_id)
- State load (session_id, source: redis/minio)
- State archive (session_id)
- State size limit exceeded (warning)

## Security Monitoring

### Audit Logging

All security-relevant events are logged:

- **Authentication attempts**: Success and failure
- **File operations**: Upload, download, delete
- **Code execution**: Language, warnings, success/failure
- **Rate limiting**: When limits are exceeded

### Log Format

```json
{
  "event_type": "authentication",
  "success": true,
  "api_key_prefix": "abc123...",
  "client_ip": "192.168.1.100",
  "endpoint": "GET /sessions",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

### Monitoring Endpoints

- **Authentication stats**: Get authentication failure statistics
- **Rate limit status**: Check current rate limit status
- **Security events**: Query recent security events

## Configuration

### Environment Variables

```bash
# API Key (required)
API_KEY=your-secure-api-key

# Resource Limits
MAX_EXECUTION_TIME=30          # seconds
MAX_MEMORY_MB=512             # megabytes
MAX_FILE_SIZE_MB=10           # megabytes per file
MAX_FILES_PER_SESSION=50      # files per session
MAX_OUTPUT_FILES=10           # output files per execution

# Redis for caching and rate limiting
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=optional-password
```

### Security Best Practices

1. **Use strong API keys**: Generate cryptographically secure random keys
2. **Enable HTTPS**: Always use HTTPS in production
3. **Monitor logs**: Regularly review security logs for suspicious activity
4. **Update dependencies**: Keep all dependencies up to date
5. **Network isolation**: Deploy in a private network when possible
6. **Resource monitoring**: Monitor resource usage and set appropriate limits

## Incident Response

### Authentication Failures

If you see repeated authentication failures:

1. Check the source IP in logs
2. Verify the API key is correct
3. Consider blocking suspicious IPs at the network level
4. Rotate API keys if compromise is suspected

### Suspicious Code Execution

If dangerous code patterns are detected:

1. Review the code content in logs
2. Check the session and user context
3. Consider additional code validation rules
4. Monitor sandbox resource usage

### File Upload Issues

For suspicious file uploads:

1. Check filename validation logs
2. Review file content if necessary
3. Verify file size and type restrictions
4. Monitor storage usage

## Security Updates

This security documentation should be reviewed and updated regularly as new threats emerge and security measures are enhanced.
