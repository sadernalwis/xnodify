#
# Evaluator classes for XNodify.
# Each class generates the nodes corresponding to the specific operator
#
# Copyright (C) 2020  Shrinivas Kulkarni
#
# License: GPL (https://github.com/Shriinivas/xnodify/blob/master/LICENSE)
#

import bpy, traceback
from mathutils import Vector

from .lookups import fnMap, mathFnMap, vmathFnMap, mathPrefix, vmathPrefix
from .lookups import reverseLookup
from .lookups import SHADER_GROUP, SHADER_MATH, SHADER_VMATH, SHADER_VALUE

class EvaluatorBase:

###################### Helpers ###############################
    @staticmethod
    def getEvaluator(id):
        if(id == '='):      return EqualsEvaluator()
        elif(id == '+'):        return PlusEvaluator()
        elif(id == '-'):        return MinusEvaluator()
        elif(id == '*'):        return MultiplyEvaluator()
        elif(id == '**'):       return PowerEvaluator()
        elif(id == '/'):        return DivisionEvaluator()
        elif(id == '%'):        return ModuloEvaluator()
        elif(id == '('):        return ParenthesisEvaluator()
        elif(id == '$'):        return DollarEvaluator()
        # elif(id == '['):      return BracketSymbol()  # Taken care in parser
        elif(id == '{'):        return BraceEvaluator()
        elif(id == 'NAME'):         return VariableEvaluator()
        elif(id == 'NUMBER'):       return NumberEvaluator()
        return None

    @staticmethod
    def getNodeDimensions(node, actual = False):
        if(node.dimensions[0] > 0 or actual == True): return node.dimensions
        def getCntForType(socket): # TODO
            if(socket.type == 'VECTOR'): return 1
            else: return 1
        if(node.bl_idname in {SHADER_MATH, SHADER_VMATH}): lookupKey = node.bl_idname + '_' + node.operation
        else: lookupKey = node.bl_idname
        customName = reverseLookup(lookupKey)
        if(customName.startswith(mathPrefix)): fnInfo = mathFnMap.get(customName)
        elif(customName.startswith(vmathPrefix)): fnInfo = vmathFnMap.get(customName)
        else: fnInfo = fnMap.get(customName)
        dimensions = fnInfo[5]
        if(node.bl_idname == SHADER_GROUP):
            socketHeight = 22
            opCnts = sum([getCntForType(o) for o in node.outputs if o.enabled == True and o.hide == False])
            ipCnt = sum([getCntForType(i) for i in node.inputs if i.enabled == True and i.hide == False])
            dimensions = (dimensions[0], dimensions[1] + (opCnts + ipCnt) * socketHeight)
        return Vector(dimensions)

    @staticmethod
    def getNode(nodeTree, customName, label = None, value = None, name = None):
        node = nodeTree.nodes.new(customName)
        if(value != None): node.outputs[0].default_value = float(value)
        if(label != None): node.label = label
        if(name != None): node.name = name
        return node

    @staticmethod
    def getPrimitiveMathNode(nodeTree, operation, label, op0, op1):
        node = EvaluatorBase.getNode(nodeTree, SHADER_MATH, label)
        node.operation = operation
        nodeTree.links.new(op0, node.inputs[0])
        nodeTree.links.new(op1, node.inputs[1])
        return node

    def __init__(self):
        pass

    def beforeOperand1(self, nodeTree, paramBus):
        return nodeTree, None

    def evaluate(self, nodeTree, group_node, paramBus, varTable):
        raise SyntaxError('Unsupported function!')

class NumberEvaluator(EvaluatorBase):
    def evaluate(self, nodeTree, group_node, paramBus, varTable):
        return EvaluatorBase.getNode(nodeTree, 'ShaderNodeValue', value = paramBus.data.value)

class VariableEvaluator(EvaluatorBase):
    def evaluate(self, nodeTree, group_node, paramBus, varTable):
        data = paramBus.data
        if(data.isFn or data.isGroup): return None # Functions are handled in evalParenthesis
        varName = data.value
        if(fnMap.get(varName) != None):
            nodeInfo = fnMap[varName]
            node = EvaluatorBase.getNode(nodeTree, nodeInfo[1], nodeInfo[2])
        elif(mathFnMap.get(mathPrefix + varName) != None):
            nodeInfo = mathFnMap[mathPrefix + varName]
            node = EvaluatorBase.getNode(nodeTree, SHADER_MATH, nodeInfo[2])
            node.operation = nodeInfo[1]
        elif(vmathFnMap.get(vmathPrefix + varName) != None):
            nodeInfo = vmathFnMap[vmathPrefix + varName]
            node = EvaluatorBase.getNode(nodeTree, SHADER_VMATH, nodeInfo[2])
            node.operation = nodeInfo[1]
        else:
            if(data.isLHS): return None # LHS is handled in evalEquals
            varTableInfo = varTable.get(varName)
            if(varTableInfo != None):
                node, sockIdx, usageCnt = varTableInfo
                if(nodeTree != node.id_data): raise SyntaxError('Groups cannot contain variables')
                if(sockIdx != None): paramBus.data.sockIdx = sockIdx
                varTable[varName][2] += 1
            else:
                node = EvaluatorBase.getNode(nodeTree, SHADER_VALUE, data.value, 0, data.value)
                varTable[varName] = [node, 0, 0]
        return node


class EqualsEvaluator(EvaluatorBase):
    def evaluate(self, nodeTree, group_node, paramBus, varTable):
        operand0 = paramBus.operand0
        operands1 = paramBus.operands1
        lhsNode = paramBus.getLHSNode()
        rhsNodes = paramBus.getRHSNodes() # len check already done
        if(lhsNode == None or (varTable.get(paramBus.operand0.value) != None and operand0.sockIdx == None)): # variable or redefined variable
            varTable[operand0.value] = [rhsNodes[0], operands1[0].sockIdx, 0]
            return rhsNodes[0]
        elif(len(lhsNode.inputs) == 0): raise SyntaxError('Left hand side should be ' + 'a node type with at least one input')
        ip = paramBus.getNodeSocket(operand0, out = False)
        i = 0 # Get the first free input of lhs
        if(len(ip.links) != 0):
            ip = lhsNode.inputs[i]
            while(i < len(lhsNode.inputs) and len(ip.links) != 0):
                ip = lhsNode.inputs[i]
                i += 1
        if(i == len(lhsNode.inputs)): raise SyntaxError('Left hand side of "=" should be a node type with at least one free input')
        op = paramBus.getNodeSocket(operands1[0])
        if(op == None): raise SyntaxError('Right hand side of "=" should be a node type with at least one output')
        nodeTree.links.new(ip, op)
        return rhsNodes[0]

class PlusEvaluator(EvaluatorBase):
    def evaluate(self, nodeTree, group_node, paramBus, varTable):
        return EvaluatorBase.getPrimitiveMathNode(nodeTree, 'ADD', 'Add', paramBus.getDefLHSOutput(), paramBus.getDefRHSOutput())

class MinusEvaluator(EvaluatorBase):
    def evaluate(self, nodeTree, group_node, paramBus, varTable):
        return EvaluatorBase.getPrimitiveMathNode(nodeTree, 'SUBTRACT', 'Subtract', paramBus.getDefLHSOutput(), paramBus.getDefRHSOutput())

class MultiplyEvaluator(EvaluatorBase):
    def evaluate(self, nodeTree, group_node, paramBus, varTable):
        return EvaluatorBase.getPrimitiveMathNode(nodeTree, 'MULTIPLY', 'Multiply', paramBus.getDefLHSOutput(), paramBus.getDefRHSOutput())

class DivisionEvaluator(EvaluatorBase):
    def evaluate(self, nodeTree, group_node, paramBus, varTable):
        return EvaluatorBase.getPrimitiveMathNode(nodeTree, 'DIVIDE', 'Divide', paramBus.getDefLHSOutput(), paramBus.getDefRHSOutput())

class ModuloEvaluator(EvaluatorBase):
    def evaluate(self, nodeTree, group_node, paramBus, varTable):
        return EvaluatorBase.getPrimitiveMathNode(nodeTree, 'MODULO', 'Modulo', paramBus.getDefLHSOutput(), paramBus.getDefRHSOutput())

class PowerEvaluator(EvaluatorBase):
    def evaluate(self, nodeTree, group_node, paramBus, varTable):
        return EvaluatorBase.getPrimitiveMathNode(nodeTree, 'POWER', 'Power', paramBus.getDefLHSOutput(), paramBus.getDefRHSOutput())

class DollarEvaluator(EvaluatorBase):
    def evaluate(self, nodeTree, group_node, paramBus, varTable):
        def setDefVal(node, val, inpIdx, valIdx, isInput):
            if(val != None):
                try: socket = node.inputs[inpIdx] if isInput else node.outputs[inpIdx]
                except: raise SyntaxError('Incorrect default value assignment')
                try:
                    try:    socket.default_value[valIdx] = float(val)
                    except: socket.default_value = float(val)
                except:
                    traceback.print_exc()
                    pass
        node = paramBus.getLHSNode()
        if(node == None): raise SyntaxError('$ should be preceded by value node name')
        dataValue = paramBus.data.value
        if(paramBus.data.symbolType == 'input'):
            for inpIdx, ipVals in enumerate(dataValue):
                if(isinstance(ipVals, list)):
                    for valIdx, ipVal in enumerate(ipVals): setDefVal(node, ipVal, inpIdx, valIdx, True)
                else: setDefVal(node, ipVals, inpIdx, 0, True)
        elif(paramBus.data.symbolType == 'output'):
            for opIdx, opVals in enumerate(dataValue):
                if(isinstance(opVals, list)):
                    for valIdx, opVal in enumerate(opVals): setDefVal(node, opVal, opIdx, valIdx, False)
                else: setDefVal(node, opVals, opIdx, 0, False)
        else: assert(False) # Should never happen
        return node

class ParenthesisEvaluator(EvaluatorBase):
    def evaluate(self, nodeTree, group_node, paramBus, varTable):
        node = customName = None
        fnName = paramBus.operand0.value
        fn = fnMap.get(fnName)
        if(fn != None):
            node = EvaluatorBase.getNode(nodeTree, fn[1], fn[2])
            customName = fnName
        else:
            fn = mathFnMap.get(mathPrefix + fnName)
            if(fn != None):
                node = EvaluatorBase.getNode(nodeTree, SHADER_MATH, fn[2])
                node.operation = fn[1]
                customName = mathPrefix + fnName
            else:
                fn = vmathFnMap.get(vmathPrefix + fnName)
                if(fn != None):
                    node = EvaluatorBase.getNode(nodeTree, SHADER_VMATH, fn[2])
                    node.operation = fn[1]
                    customName = vmathPrefix + fnName
        if(node != None):
            outputs = paramBus.getRHSOutputs()
            inputs = [ip for ip in node.inputs if ip.enabled == True]
            for i in range(min(len(outputs), len(inputs))):
                if(inputs[i] != None and outputs[i] != None):
                    nodeTree.links.new(outputs[i], inputs[i])
            return node
        raise SyntaxError('Unknown Function: '+ paramBus.operand0.value)

class BraceEvaluator(EvaluatorBase):

    def beforeOperand1(self, nodeTree, paramBus):
        if(paramBus.operand0 == None): groupName = 'XNGroup'
        else: groupName = paramBus.operand0.value
        group = nodeTree.nodes.new(SHADER_GROUP)
        group.name = groupName
        gNodeTree = bpy.data.node_groups.new(groupName, 'ShaderNodeTree')
        group.node_tree = gNodeTree
        gNodeTree.nodes.new('NodeGroupOutput')
        gNodeTree.nodes[-1].name = gNodeTree.nodes[-1].label = 'Group Output'
        gNodeTree.nodes.new('NodeGroupInput')
        gNodeTree.nodes[-1].name = gNodeTree.nodes[-1].label = 'Group Input'
        paramBus.groupNode = group # Temporarily created will be used below
        return gNodeTree, group

    def evaluate(self, tree, group_node, paramBus, varTable):
        nodes = tree.nodes
        links = tree.links
        gOutput = nodes[0]
        gInput = nodes[1]
        childNodes = set()
        for op in paramBus.operands1:
            childOps = []
            op.getLinearList(childOps)
            childNodes = childNodes.union([o.node for o in childOps if o != None and o.node != None])
        for node in childNodes:
            outputs = [o for o in node.outputs if o.enabled == True and o.hide == False]
            for op in outputs:
                if(len(op.links) == 0):
                    gIp = tree.outputs.new(op.bl_idname, op.name)
                    links.new( op, gOutput.inputs[-2])
        for node in childNodes:
            inputs = [i for i in node.inputs if i.enabled == True and i.hide == False]
            for ip in inputs:
                if(len(ip.links) == 0):
                    gOp = tree.inputs.new(ip.bl_idname, ip.name)
                    links.new(ip, gInput.outputs[-2])
        return paramBus.groupNode # Created above in beforeOperand1 method
