---

## ⚠️ CRITICAL: PREVIOUS ITERATION FAILED - SELF-CORRECTION REQUIRED

The previous QA session failed with the following error:

**Error**: ${error_message}
**Consecutive Failures**: ${consecutive_errors}

### What Went Wrong

You did NOT update the `implementation_plan.json` file with the required `qa_signoff` object.

### Required Action

After completing your QA review, you MUST:

1. **Read the current implementation_plan.json**:
   ```bash
   cat ${spec_dir}/implementation_plan.json
   ```

2. **Update it with your qa_signoff** by editing the JSON file to add/update the `qa_signoff` field:

   If APPROVED:
   ```json
   {
     "qa_signoff": {
       "status": "approved",
       "timestamp": "[current ISO timestamp]",
       "qa_session": ${qa_session},
       "report_file": "qa_report.md",
       "tests_passed": {"unit": "X/Y", "integration": "X/Y", "e2e": "X/Y"},
       "verified_by": "qa_agent"
     }
   }
   ```

   If REJECTED:
   ```json
   {
     "qa_signoff": {
       "status": "rejected",
       "timestamp": "[current ISO timestamp]",
       "qa_session": ${qa_session},
       "issues_found": [
         {"type": "critical", "title": "[issue]", "location": "[file:line]", "fix_required": "[description]"}
       ],
       "fix_request_file": "QA_FIX_REQUEST.md"
     }
   }
   ```

3. **Use the Edit tool or Write tool** to update the file. The file path is:
   `${spec_dir}/implementation_plan.json`

### FAILURE TO DO THIS WILL CAUSE ANOTHER ERROR

This is attempt ${attempt_num}. If you fail to update implementation_plan.json again, the QA process will be escalated to human review.

---
