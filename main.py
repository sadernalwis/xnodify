#
# Main module of XNodify.
# Includes classes that create and arrange nodes from parsed tokens.
# TODO: Maybe 2 modules for processing and post-processing
#
# Copyright (C) 2020  Shrinivas Kulkarni
#
# License: GPL (https://github.com/Shriinivas/xnodify/blob/master/LICENSE)
#

import bpy
from mathutils import Vector

from .lookups import getCombinedMap, SHADER_GROUP

# For debug
from . import parser, lookups, evaluator
import importlib
importlib.reload(parser)
importlib.reload(lookups)
importlib.reload(evaluator)

from . evaluator import NumberEvaluator, VariableEvaluator, EqualsEvaluator
from . evaluator import PlusEvaluator, MultiplyEvaluator, DivisionEvaluator
from . evaluator import PowerEvaluator, ParenthesisEvaluator, EvaluatorBase

# Message bus to exchange data between objects
class EvalParamsBus:
    @staticmethod
    def getNodeSocket(data, node, out = True, defaultIdx = 0):
        if(data == None or node == None):
            return None
        if(out):
            sockets = [o for o in node.outputs \
                if o.enabled == True and o.hide == False]
        else:
            sockets = [i for i in node.inputs \
                if i.enabled == True and i.hide == False]

        if(data.sockIdx == None and len(sockets) > 0):
            return sockets[defaultIdx] if(defaultIdx != None and \
                len(sockets) > defaultIdx) else None

        socket = None
        try:
            socket = sockets[int(data.sockIdx)]
        except Exception as e:
            print(e)
            try:
                socket = sockets[data.sockIdx]
            except Exception as e2:
                print(e2)
                if(len(node.outputs) > 0):
                    socket = sockets[0]
        return socket

    def __init__(self, data, operand0, operands1):
        self.data = data
        self.operand0 = operand0
        self.operands1 = operands1
        self.lhsNode = None
        self.rhsNodes = None

    def setLHSNode(self, lhsNode):
        self.lhsNode = lhsNode

    def setRHSNodes(self, rhsNodes):
        self.rhsNodes = rhsNodes

    def getDefLHSOutput(self):
        if(self.lhsNode != None and self.operand0 != None):
           return EvalParamsBus.getNodeSocket(self.operand0, self.lhsNode, True)
        return None

    def getRHSOutputs(self):
        if(self.operands1 == None):
            return None
        outputs = []
        for i, s in enumerate(self.operands1):
            socket = EvalParamsBus.getNodeSocket(s, self.rhsNodes[i])
            outputs.append(socket)
        return outputs

    def getDefRHSOutput(self):
        outputs = self.getRHSOutputs()
        return None if outputs == None else outputs[0]

class SymbolData(object):
    def __init__(self, id, meta, value):
        self.meta = meta
        self.value = value
        self.operand0 = self.operand1 = None
        # self.operand2... Ternary not supported for now
        self.isFn = False # TODO: Separate class? (part of meta actually)
        self.isGroup = False # TODO: Separate class? (part of meta actually)
        self.sockIdx = None # Index in [] operator TODO: separate class?
        self.isLHS = False # TODO: Separate class?
        self.symbolType = None # For default values i.e. $ & { symbols
        self.evaluator = EvaluatorBase.getEvaluator(id)

    def __repr__(self):
        return str(self.value) + ' ' + str(self.meta.id)

    def getLinearList(self, items):
        items.append(self)
        if isinstance(self.operand0,  SymbolData):
            self.operand0.getLinearList(items)

        if isinstance(self.operand1,  SymbolData):
            operands1 = [self.operand1]
        elif isinstance(self.operand1,  list):
            operands1 = self.operand1
        else:
            operands1 = []

        for operand in operands1:
            if(operand != None):
                operand.getLinearList(items)

        return items

    def getMetaData(self):
        return self.meta

    # Assuming operand0 i.e. LHS of the operator will always be a single element
    # operand1 can be a list (function arguments for example);
    # So in case of prefix operators with a list as operand0,
    # this will need to be changed
    def evalSymbol(self, nodeTree, varTable, afterProcNode, colNo = 0):
        if(self.evaluator == None):
            return None

        operand0 = self.operand0

        if isinstance(operand0,  list):
            raise SyntaxError(self.getMetaData().id + \
                ' expression does not evaluate to a node')
        elif isinstance(operand0,  SymbolData):
            # TODO: Hack...a better way to achieve this
            if(self.getMetaData().id in {'='}):
                nextColNo = colNo
            else:
                nextColNo = colNo + 1
            lhsNode = operand0.evalSymbol(nodeTree, varTable, \
                afterProcNode, nextColNo)
        else:
            lhsNode = None

        if isinstance(self.operand1,  SymbolData):
            operands1 = [self.operand1]
        elif isinstance(self.operand1,  list):
            operands1 = self.operand1
        else:
            operands1 = None


        paramBus = EvalParamsBus(self, operand0, operands1)
        paramBus.setLHSNode(lhsNode)

        nodeTree = self.evaluator.beforeOperand1(nodeTree, paramBus)

        nextColNo = colNo + 1
        if(operands1 == None):
            rhsNodes = None
        else:
            rhsNodes = []
            for s in operands1:
                if(s != None):
                    rhsNode = s.evalSymbol(nodeTree, varTable, \
                        afterProcNode, nextColNo)
                    rhsNodes.append(rhsNode)
                else:
                    rhsNodes.append(None)

        paramBus.setRHSNodes(rhsNodes)

        node = self.evaluator.evaluate(nodeTree, paramBus, varTable)

        # afterProcNode: callback after processing each token
        afterProcNode(colNo, node, paramBus, varTable)

        return node

class VarInfo:
    def __init__(self, nodeTreeTable):
        self.nodeTreeTable = nodeTreeTable
        self.usageLines = set()
        self.isProcessed = False # Used at the time of processing
        self.isLaidOut = False # Used at the time of post-processing

    def __str__(self):
        return '<' + str(self.nodeTreeTable) + '::' + str(self.usageLines) + '>'

    def __repr__(self):
        return str(self)

# Post-processing...
# At this point all nodes are already created and links established
class NodeLayout:
    noodleWidth = 80

    # Go through each node of the parent graph and expand the var nodes
    # into the corresponding row and column; i.e. insert nodes of the var
    # node graph at its place.
    # Mark the var node as processed only if the current line is being
    # displayed, so that it won't be processed again.
    # (Remember: this is just for arranging the nodes,
    # linking happened already in evalSymbol)
    # TODO: 1) linear search? 2) NodeGraph traversed & recreated too often
    @staticmethod
    def insertVarNodes(nodeTreeTable, parentNodeTree, varNodeGraphs, \
        currLineNo, markProcessed):
        parentNodeGraph = nodeTreeTable[parentNodeTree]
        newNodeGraph = {}
        colDiff = 0
        for col in sorted(parentNodeGraph.keys()):
            for row in range(len(parentNodeGraph[col])):
                node = parentNodeGraph[col][row]
                varInfo = varNodeGraphs.get(node)
                if(varInfo != None and len(varInfo.usageLines) > 0 and \
                    max(varInfo.usageLines) == currLineNo):
                    varTreeTable = varInfo.nodeTreeTable
                    for key in varTreeTable.keys():
                        # There will be only one key == node.id_data
                        if(key != node.id_data):
                            # Node of a group, no variables allowed, so skip
                            continue
                        varNodeGraph = varTreeTable[key]
                        for varColNo in sorted(varNodeGraph.keys()):
                            varCol = varNodeGraph[varColNo]
                            for varRowNo in reversed(range(len(varCol))):
                                varNode = varNodeGraph[varColNo][varRowNo]
                                if(varNode.bl_idname == SHADER_GROUP and \
                                    varNode.node_tree in varTreeTable.keys()):
                                    nodeTreeTable[varNode.node_tree] = \
                                        varTreeTable[varNode.node_tree]
                                prevVarInfo = varNodeGraphs.get(varNode)
                                if(prevVarInfo == None or \
                                    prevVarInfo.isLaidOut == False):
                                    newColNo = col + colDiff + varColNo
                                    nodeColumn = newNodeGraph.get(newColNo)
                                    if(nodeColumn == None):
                                        newNodeGraph[newColNo] = []
                                        nodeColumn = newNodeGraph[newColNo]
                                    nodeColumn.append(varNode)
                                    if(prevVarInfo != None and markProcessed):
                                        prevVarInfo.isLaidOut = True
                        # ~ colDiff += len(varNodeGraph.keys())
                    if(markProcessed):
                        varInfo.isLaidOut = True
                else:
                    newColNo = col + colDiff
                    nodeColumn = newNodeGraph.get(newColNo)
                    if(nodeColumn == None):
                        newNodeGraph[newColNo] = []
                        nodeColumn = newNodeGraph[newColNo]
                    nodeColumn.append(node)
        return newNodeGraph

    @staticmethod
    def arrangeNodes(nodeTreeTable, nodeTree, location, scale, alignment):

        height = 0
        width = 0
        augNodeGraph = nodeTreeTable[nodeTree]

        nodeLayout = NodeLayout(augNodeGraph)

        colHeights, colWidths, totalHeight, totalWidth, nodeGraph = \
            nodeLayout.colHeights, nodeLayout.colWidths, \
                nodeLayout.totalHeight, nodeLayout.totalWidth, \
                    nodeLayout.nodeGraph

        for col in range(len(nodeGraph)):
            yOffset = 0
            if(alignment == 'CENTER'):
                yOffset = (totalHeight - colHeights[col]) / 2
            elif(alignment == 'BOTTOM'):
                yOffset = (totalHeight - colHeights[col])

            prevHeight = 0
            for row in range(len(nodeGraph[col])):
                node = nodeGraph[col][row]
                dimensions = EvaluatorBase.getNodeDimensions(node)
                x = totalWidth / 2 -  sum(colWidths[:col + 1]) - \
                    col * NodeLayout.noodleWidth + \
                        (colWidths[col] - dimensions[0]) / 2
                y =  prevHeight + yOffset
                prevHeight += dimensions[1]

                nodeLoc = Vector((location[0] + scale[0] * x, \
                    location[1] + -scale[1] * y))
                node.location = nodeLoc

                if(node.bl_idname == SHADER_GROUP and \
                    node.node_tree in nodeTreeTable.keys()):
                    refLocation =  -node.id_data.view_center + nodeLoc
                    newLayout = \
                        NodeLayout.arrangeNodes(nodeTreeTable, node.node_tree, \
                            refLocation, scale, alignment)
                    gTotalWidth = newLayout.totalWidth
                    nOut = node.node_tree.nodes['Group Output']
                    nOut.location = refLocation
                    nOut.location[0] += \
                        NodeLayout.noodleWidth + gTotalWidth / 2
                    nIn = node.node_tree.nodes['Group Input']
                    nIn.location = refLocation
                    nIn.location[0] -= NodeLayout.noodleWidth + \
                        gTotalWidth / 2 + \
                            EvaluatorBase.getNodeDimensions(nIn)[0]
        return nodeLayout

    # Just to confirm that Blender finished displaying the node and
    # dimension values are populated
    @staticmethod
    def testNodeDimension(nodeTreeTable, nodeTree):
        nodeGraph = nodeTreeTable[nodeTree]
        dimensions = None
        for col in sorted(nodeGraph.keys()):
            for row in range(len(nodeGraph[col])):
                node = nodeGraph[col][row]
                dimensions = EvaluatorBase.getNodeDimensions(node, True)
                break
            break
        return dimensions

    @staticmethod
    def arrangeNodeLines(dispTreeTables, matNodeTree, \
        location, scale, alignment, addFrame, frameTitle):
        height = 0
        frameHeight = (70 if addFrame else 30) * scale[1]
        for dispTreeTable in dispTreeTables:
            lineNo = dispTreeTable[0]
            nodeTreeTable = dispTreeTable[1]
            newLoc = Vector(location) + Vector((0, -height))
            nodeLayout = NodeLayout.arrangeNodes(nodeTreeTable, \
                matNodeTree, newLoc, scale, alignment)
            if(addFrame):
                frame = matNodeTree.nodes.new(type='NodeFrame')
                frame.label = frameTitle if frameTitle != None \
                    else 'Line ' + str((lineNo))
                for col in range(len(nodeLayout.nodeGraph)):
                    for row in range(len(nodeLayout.nodeGraph[col])):
                        nodeLayout.nodeGraph[col][row].parent = frame
            height += nodeLayout.totalHeight + frameHeight


    def __init__(self, tNodeGraph):
        self.colHeights = []
        self.colWidths = []
        self.nodeGraph = []

        # Normalize the nodegraph to remove gaps in columns and rows
        for col in sorted(tNodeGraph.keys()):
            self.nodeGraph.append([])
            self.colHeights.append(0)
            self.colWidths.append(0)
            for row in range(len(tNodeGraph[col])):
                node = tNodeGraph[col][row]
                self.nodeGraph[-1].append(node)
                dimensions = EvaluatorBase.getNodeDimensions(node)
                self.colHeights[-1] += dimensions[1]
                if(self.colWidths[-1] < dimensions[0]):
                    self.colWidths[-1] = dimensions[0]

        self.nodeCnt = len(self.colHeights)
        self.totalHeight = max(self.colHeights) if(self.nodeCnt > 0) else 0
        self.totalWidth = (sum(self.colWidths) + \
            (self.nodeCnt - 1) * NodeLayout.noodleWidth) \
                if(self.nodeCnt > 0) else 0

# One Controller per line
class Controller:
    def __init__(self, globalNodes, varNodeGraphs, currLineNo):
        self.globalNodes = globalNodes

        # varNodeGraphs is used in layout. here only usage count is updated
        # based on currLineNo
        self.varNodeGraphs = varNodeGraphs
        self.currLineNo = currLineNo

        # This will have nodegraphs for global as well as group node tree
        self.nodeTreeTable = {}

    def getGlobalNodes(self):
        allNodes = set()
        for nodeTree in self.nodeTreeTable.keys():
            nodeGraph = self.nodeTreeTable[nodeTree]
            for col in nodeGraph.keys():
                allNodes = allNodes.union(nodeGraph[col])
        allNodes = allNodes.union(self.globalNodes)
        return allNodes

    def removeAllNodes(self, newNodes = None):
        for node in self.getGlobalNodes():
            node.id_data.nodes.remove(node)
        # TODO : better way to delete all nodes on syntax error
        if(newNodes != None):
            for node in newNodes:
                try:
                    node.id_data.nodes.remove(node)
                except:
                    pass

    # Callback method, creates and updates nodeTreeTable used by arrange.
    # nodeTreeTable will have mulitple nodeGraphs only in case of group nodes.
    def afterProcNode(self, colNo, node, params, varTable):
        varInfo = self.varNodeGraphs.get(node)
        if(varInfo != None):
            varInfo.usageLines.add(self.currLineNo)
        # Add varNode only once in nodeGraph, so that it's locatable later
        if((varInfo != None and not varInfo.isProcessed) or \
            (node != None and node not in self.getGlobalNodes())):
            nodeTree = node.id_data
            nodeGraph = self.nodeTreeTable.get(nodeTree)
            if(nodeGraph == None):
                self.nodeTreeTable[nodeTree] = {}
                nodeGraph = self.nodeTreeTable[nodeTree]
            nodeColumn = nodeGraph.get(colNo)
            if(nodeColumn == None):
                nodeGraph[colNo] = []
                nodeColumn = nodeGraph[colNo]
            nodeColumn.append(node)
            if(varInfo != None):
                varInfo.isProcessed = True

    def createNodes(self, nodeTree, varTable, expression, depth = 0):

        OUTPUT_ON_LHS = 'Deprecation Warning: output on LHS is deprecated, ' + \
            'use output on RHS with incoming nodes as parameters instead.' + \
            '(Layout won\'t be correct.)'
        warnings = set()
        dataTree = parser.parse(expression, SymbolData)

        if(dataTree == None):
            return None, None, None, None, warnings

        datas = dataTree.getLinearList([])
        equalsOps = [t for t in datas if t.getMetaData().id == '=']

        if(len(equalsOps) > 1):
            raise SyntaxError('Only one assignment allowed in a line.')
        elif(len(equalsOps) == 1):
            op0 = equalsOps[0].operand0
            if(op0.getMetaData().id != 'NAME'):
                raise SyntaxError('LHS must be a variable or the output node')
            if(op0.value == 'output'):
                warnings.add(OUTPUT_ON_LHS)
                exprType = 'output'
            elif(op0.value in getCombinedMap().keys()):
                raise SyntaxError('LHS cannot refer to a node ' + \
                    'other than output')
            else:
                exprType = op0.value
        elif(len(equalsOps) == 0):
            exprType = None

        evalNode = dataTree.evalSymbol(nodeTree, varTable, \
            self.afterProcNode, depth)
        varInfo = varTable.get(exprType)
        if(varInfo != None and \
            varInfo[0].bl_idname == 'ShaderNodeOutputMaterial'):
            warnings.add(OUTPUT_ON_LHS)
            exprType = 'output'
        return evalNode, exprType, self.nodeTreeTable, \
            self.getGlobalNodes(), warnings

class DisplayParams:
    def __init__(self, dispTreeTables, matNodeTree, \
        location, scale, alignment, addFrame, frameTitle, warnings):

        self.dispTreeTables = dispTreeTables
        self.matNodeTree = matNodeTree
        self.location = location
        self.scale = scale
        self.alignment= alignment
        self.addFrame = addFrame
        self.frameTitle = frameTitle
        self.warnings = warnings

# Context for all the lines
class XNodifyContext:

    @staticmethod
    def hardReplace(expression, hardReplaceTable):
        for key in hardReplaceTable:
            expression = expression.replace('`'+key+'`', hardReplaceTable[key])
        return expression

    @staticmethod
    def isLineDisplayed(nType, varTable):
        return nType == 'line' or (varTable.get(nType) != None and \
        (varTable.get(nType)[2] == 0 or \
            varTable.get(nType)[0].bl_idname == 'ShaderNodeOutputMaterial'))

    def __init__(self):
        pass

    def processExpressions(self, lineFeeder, matNodeTree, \
        location, scale, alignment, addFrame, frameTitle = None):

        if(matNodeTree == None):
            matNodeTree = XNodifyContext.getActiveMatTree()

        actLineCnt = 1
        warnings = {}
        varNodeGraphs = {}
        varTable = {}
        hardReplaceTable = {}
        allNodes = set()
        lineNodeTables = []
        lineCnt = 0

        # TODO: Split in 3 different methods
        try:
            expression = next(lineFeeder)
            while(expression != None):
                expression = expression.strip()
                expression = XNodifyContext.hardReplace(expression, \
                    hardReplaceTable)
                controller = Controller(allNodes, varNodeGraphs, lineCnt)
                evalNode, exprType, nodeTreeTable, newNodes, newWarnings = \
                    controller.createNodes(matNodeTree, varTable, expression)

                if(len(newWarnings) > 0):
                    warnings[actLineCnt] = newWarnings

                if(exprType != None):
                    lhs, rhs = expression.split('=')
                    hardReplaceTable[lhs.strip()] = rhs.strip()

                if(nodeTreeTable != None and len(nodeTreeTable) > 0):
                    if(exprType != None and exprType != 'output'):
                        if(varNodeGraphs.get(evalNode) == None):
                            varNodeGraphs[evalNode] = VarInfo(nodeTreeTable)
                        else:
                            nodeTreeTable = varNodeGraphs[evalNode].nodeTreeTable
                        nType = exprType
                    else:
                        allNodes = allNodes.union(newNodes)
                        nType = 'line'
                    lineNodeTables.append((nType, nodeTreeTable, \
                        actLineCnt, evalNode))
                    lineCnt += 1
                expression = next(lineFeeder)
                actLineCnt += 1

            varKeys = varNodeGraphs.keys()

            dispTreeTables = []
            for i in range(lineCnt):
                nType, nodeTreeTable, actLineCnt, evalNode = lineNodeTables[i]
                isDisplayed = XNodifyContext.isLineDisplayed(nType, varTable)
                augNodeGraph = NodeLayout.insertVarNodes(nodeTreeTable, \
                    matNodeTree, varNodeGraphs, i, isDisplayed)
                nodeTreeTable[matNodeTree] = augNodeGraph
                if(isDisplayed):
                    dispTreeTables.append((actLineCnt, nodeTreeTable))

            # ~ NodeLayout.arrangeNodeLines(dispTreeTables, matNodeTree, \
                # ~ location, scale, alignment, addFrame, frameTitle)
            displayParams = DisplayParams(dispTreeTables, matNodeTree, \
                location, scale, alignment, addFrame, frameTitle, warnings)

            return displayParams

        except Exception as e:
            controller.removeAllNodes(varNodeGraphs.keys())
            raise SyntaxError('Line: ' + str(actLineCnt) + ': ' + str(e))

def getActiveMatTree():
    obj = bpy.context.active_object
    if(obj == None):
        return None
    mat = obj.active_material
    if(mat == None):
        return None

    mat.use_nodes = True
    return mat.node_tree

def procScript(scriptName, location, scale, alignment, addFrame):
    def scriptLineFeeder(scriptName):
        for line in bpy.data.texts[scriptName].lines:
            yield line.body
        yield None

    return XNodifyContext().processExpressions(scriptLineFeeder(scriptName), \
        getActiveMatTree(), location, scale, alignment, addFrame)

def procFile(filePath, location, scale, alignment, addFrame):
    def fileLineFeeder(filePath):
        with open(filePath) as f:
            line = f.readline()
            while(line):
                yield line
                line = f.readline()
        yield None

    return XNodifyContext().processExpressions(fileLineFeeder(filePath), \
        getActiveMatTree(), location, scale, alignment, addFrame)

def procSingleExpression(expression, location, scale, alignment, addFrame):
    def feeder():
        for e in [expression, None]:
            yield e
    return XNodifyContext().processExpressions(feeder(), getActiveMatTree(), \
        location, scale, alignment, addFrame, 'Expression')

