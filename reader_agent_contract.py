"""连接说明、下载路由和机器权限共用同一份客户端模块清单。"""
CLIENT_MODULES=('reader_term_quality.py','reader_writer_format.py','reader_writer_patch.py','reader_writer_schema.py','reader_source_cache.py','reader_writer_input.py','reader_practice_text.py','reader_practice_policy.py','reader_writer_profiles.py','reader_contract.py','reader_tool_audit.py','reader_credentials.py','reader_generation.py','reader_quality.py','reader_translation.py','reader_prompts.py','reader_media.py','reader_bridge.py','reader_worker.py','reader_execute.py','reader_term_names.py','reader_sequence.py','reader_connected.py','reader_blocks.py','reader_lesson_checks.py','objective_grading.py','reader_writer_transport.py')
CLIENT_PATHS=frozenset('/api/agent/'+name for name in CLIENT_MODULES)

CLIENT_DEPENDENCIES=('requests==2.34.2','zstandard==0.25.0','Pillow==12.2.0')
