from fastapi import FastAPI, Body
from fastapi.responses import ORJSONResponse
from .openwebui_bridge import handle_chat
from ..logging import setup_logger

app = FastAPI()
log = setup_logger()

@app.post("/chat", response_class=ORJSONResponse)
def chat_endpoint(payload: dict = Body(...)):
    try:
        return handle_chat(payload)
    except Exception as e:
        log.exception("Error in /chat")
        return ORJSONResponse({"error": str(e)}, status_code=500)

def main():
    import uvicorn
    uvicorn.run("genomeer.adapters.server:app", host="0.0.0.0", port=8099, reload=False)
