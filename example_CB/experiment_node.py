#!/usr/bin/python3
#coding=utf-8

from core.node import *
from core.data_structure import Dict

#=======================================================================================================================
class ExperimentForwarderUnit(ForwarderUnitBase):
    def __init__(self, outI_cd):
        super().__init__()
        self.outI_cd= outI_cd # out interest cool delay

    def install(self, announces, api):
        super().install(announces, api)
        #发布的 Announce
        self.publish['unsatisfied'].append( announces['unsatisfied'] ) # TODO
        self.publish['unsolicited'].append( announces['unsolicited'] )

    def _inPacket(self, faceid, packet):
        if packet.type == Packet.TYPE.INTEREST:
            self._inInterest(faceid, packet)
        elif packet.type == Packet.TYPE.DATA:
            self._inData(faceid, packet)
        else:pass


    def _inInterest(self, faceid, packet):
        if len(packet.pathI) > 0:# 转发
            info= self.api['Info::getInfo'](packet) #已有记录: info[faceid].inI == clock.time()
            sendid, packet.pathI= packet.pathI[0], packet.pathI[1:] #将头部摘取出来, 基于net的设定, NodeName= FaceID
            if not self.isOutICooling( info[sendid] ):
                self.api['Face::send']( {sendid}, packet )
        else:
            data= self.api['CS::match'](packet)
            if data is not None:#hit
                self.api['Face::send']( {faceid}, data ) #记录: info[send_id].outD == clock.time()
            else:#miss
                path= self.api['Net::getPath'](packet)
                log.waring('本该有CS却不存在, 新路由', path)
                packet.pathI= path[1:]
                self._inInterest(faceid, packet)# 单纯为了代码复用


    def _inData(self, faceid, packet):
        info= self.api['Info::getInfo'](packet) #已有记录: info[faceid].inI == clock.time()
        send_ids= [ id for id in info if\
            self.isPending( info[id] )\
        ]# Pending. 遍历info而非所有id, 是因为pengding的id一定在info中有记录

        if len(send_ids) > 0:
            self.api['Face::send'](send_ids, packet) #记录: info[send_id].outI == clock.time()
            self.api['CS::store']( packet ) #FIXME 未经请求包储存??
        else:
            self.publish['unsolicited'](faceid, packet)

    #--------------------------------------------------------------------------
    def isPending(self, entry):#face同时接收到I和D, 该face不算Pending
        return entry.recv[Packet.TYPE.DATA] < entry.recv[Packet.TYPE.INTEREST] \
               and \
               entry.send[Packet.TYPE.DATA] < entry.recv[Packet.TYPE.INTEREST]

    def isOutICooling(self, entry):
        return entry.send[Packet.TYPE.DATA] < entry.send[Packet.TYPE.INTEREST] \
               and \
               entry.recv[Packet.TYPE.DATA] < entry.send[Packet.TYPE.INTEREST] \
               and \
               clock.time() < entry.send[Packet.TYPE.INTEREST] + self.outI_cd


#-----------------------------------------------------------------------------------------------------------------------
class ExperimentAppUnit(AppUnitBase):
    def __init__(self):
        super().__init__()
        self.pending= Dict()

    def _ask(self, packet):
        if packet.type == Packet.TYPE.INTEREST:
            self.pending.setdefault( packet.name, [] ) #[] 请求时间列表
            self.pending[packet.name].append( clock.time() )

            # 距离等于路径节点间隔, 如[1,2,3], distance== A -(1)-> B -(2)-> C ==2
            path= self.api['Net::getPath'](packet)
            setattr(packet, 'pathI', path[1:] )#不要第0位, 去掉路劲中的当前节点名

            # publish 要先于 app_channel 调用
            # 1: 避免app_channel中修改packet
            # 2: 避免publish['_respond']先于publish['_ask']产生(CS命中的情况下)
            self.publish['ask']( packet, len(packet.pathI) )
            self.app_channel(packet)# 发送packet


    def _respond(self, packet):
        if packet.type == Packet.TYPE.DATA  and  packet.name in self.pending:
            ask_time_list= self.pending.pop(packet.name)
            for ask_time in ask_time_list:
                if clock.time()-ask_time > 200:#200 网络最大响应时间
                    # FIXME 注意, 如果环路问题没解决, 会出现以下问题
                    # t0: I-> 但是loop, t10000: I->, t10010: <-D responed为[t1, t10000] 则 响应时间为[10010, 10]
                    # 所以需要设置个时间来取出合理响应时间,
                    log.error(label[self], '超时', ask_time_list)
                else:
                    self.publish['respond'](packet, ask_time)


#-----------------------------------------------------------------------------------------------------------------------
from core.cs import SimulatCSUnit
from core.face import FaceUnit, RepeatChecker
from core.info_table import InfoUnit
from core.policy import FIFOPolicy

class NoLoopChecker:# 因为路由策略的保障, 不检查兴趣包的循环
    def isLoop(self, packet):
        return False


class ExperimentNode(NodeBase):
    def __init__(self):
        super().__init__()
        self.install( 'faces',  FaceUnit( NoLoopChecker(), RepeatChecker() ) )
        self.install( 'info',   InfoUnit(max_size= 2, life_time= 100000) )

        # capacity=1: 实验为一个包测试, life_time=None:使得必须在之后被设置
        self.install( 'cs',     SimulatCSUnit(capacity= 1, life_time= None))
        self.install( 'policy', FIFOPolicy() )
        self.install( 'app',    ExperimentAppUnit() )

        #100来自于100*100网格平均响应时间
        self.install( 'fwd',    ExperimentForwarderUnit(outI_cd= 100) )

        if IS_DEBUG:
            self.install( 'log', LogUnit() )

#=======================================================================================================================
