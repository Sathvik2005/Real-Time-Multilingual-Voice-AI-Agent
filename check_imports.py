import sys, traceback

modules = [
    'backend.config',
    'backend.database.connection',
    'backend.database.models',
    'backend.database.crud',
    'backend.memory.session',
    'backend.services.language_detection',
    'backend.services.translation',
    'backend.tools.appointment_tools',
    'backend.tools.doctor_tools',
    'backend.agent.nodes',
    'backend.agent.graph',
    'backend.voice_pipeline.stt',
    'backend.voice_pipeline.tts',
    'backend.voice_gateway.stream_manager',
    'backend.voice_gateway.websocket_handler',
    'backend.main',
]

for m in modules:
    try:
        __import__(m)
        print(f'OK  {m}')
    except Exception as e:
        print(f'ERR {m}:')
        traceback.print_exc()
        print()
