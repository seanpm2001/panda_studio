import grpc
import concurrent.futures as futures
import os

from pandare import Panda

# protobuf compiler is stupid
import sys
sys.path.append(".")
sys.path.append("pb")

import pb.panda_agent_pb2_grpc as pb_grpc
import pb.panda_agent_pb2 as pb
from agent import PandaAgent
from agent import ErrorCode
from time import sleep

PORTS = [
    "[::]:50051",
    "unix:///panda/shared/panda-agent.sock"
]

executor = futures.ThreadPoolExecutor(max_workers=10)

class PandaAgentServicer(pb_grpc.PandaAgentServicer):
    def __init__(self, server, agent: PandaAgent):
        self.server = server
        self.agent = agent
    
    def StartAgent(self, request: pb.StartAgentRequest, context):
        if self.agent.panda.started.is_set():
            raise RuntimeError(ErrorCode.RUNNING.value, "Cannot start another instance of PANDA while one is already running")
        # start panda in a new thread, because qemu blocks this thread otherwise
        executor.submit(self.agent.start)
        sleep(0.5) # ensures internal flags get set
        return pb.StartAgentResponse()
    
    def StopAgent(self, request: pb.StopAgentRequest, context):
        self.agent.stop()
        self.server.stop(grace=5)
        return pb.StopAgentResponse()
    
    def RunCommand(self, request: pb.RunCommandRequest, context):
        output = self.agent.run_command(request.command)
        return pb.RunCommandResponse(statusCode=0, output=output)
    
    def StartRecording(self, request: pb.StartRecordingRequest, context):
        self.agent.start_recording(recording_name=request.recording_name)
        return pb.StartRecordingResponse()
    
    def StopRecording(self, request: pb.StopRecordingRequest, context):
        recording_name = self.agent.stop_recording()
        return pb.StopRecordingResponse(
            recording_name=recording_name,
            snapshot_filename=f"{recording_name}-rr-snp",
            ndlog_filename=f"{recording_name}-rr-nondet.log"
        )

    def StartReplay(self, request: pb.StartReplayRequest, context):
        if self.agent.panda.started.is_set(): 
            raise RuntimeError(ErrorCode.RUNNING.value, "Cannot start another instance of PANDA while one is already running")
        serial = self.agent.start_replay(request.recording_name)
        with (open("./shared/execution.log")) as file:
            replay = file.read()
        return pb.StartReplayResponse(serial=serial, replay=replay)

    def StopReplay(self, request: pb.StopReplayRequest, context):
        serial = self.agent.stop_replay()
        with (open("./shared/execution.log")) as file:
            replay = file.read()
        return pb.StopReplayResponse(serial=serial, replay=replay)

    def SendNetworkCommand(self, request: pb.NetworkRequest, context):
        
        response = self.agent.execute_network_command(request)

        return pb.NetworkResponse(0, response)


def serve():
    #TODO remove hardcoding to replace with param solution and move into agent
    if(os.path.isfile("/panda/shared/system_image.qcow2")):
        panda = Panda(arch='x86_64', qcow='/panda/shared/system_image.qcow2', mem='1024',
                 os='linux-64-ubuntu:4.15.0-72-generic-noaslr-nokaslr', expect_prompt='root@ubuntu:.*# ',
                 extra_args='-display none')
    else:
        panda = Panda(generic='x86_64')

    agent = PandaAgent(panda)
    server = grpc.server(executor)
    pb_grpc.add_PandaAgentServicer_to_server(
        PandaAgentServicer(server, agent), server)

    for port in PORTS:
        server.add_insecure_port(port)
        print(f'panda agent grpc server listening on port {port}')
    server.start()
    server.wait_for_termination()

if __name__ == "__main__":
    serve()