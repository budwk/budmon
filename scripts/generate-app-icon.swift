import Foundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers
let space = CGColorSpaceCreateDeviceRGB()
let context = CGContext(data:nil,width:1024,height:1024,bitsPerComponent:8,bytesPerRow:0,space:space,bitmapInfo:CGImageAlphaInfo.noneSkipLast.rawValue)!
let colors = [CGColor(red:0.035,green:0.06,blue:0.105,alpha:1),CGColor(red:0.08,green:0.34,blue:0.36,alpha:1)]
let gradient = CGGradient(colorsSpace:space,colors:colors as CFArray,locations:[0,1])!
context.drawLinearGradient(gradient,start:CGPoint(x:0,y:0),end:CGPoint(x:1024,y:1024),options:[.drawsBeforeStartLocation,.drawsAfterEndLocation])
context.setStrokeColor(CGColor(red:0.35,green:0.95,blue:0.78,alpha:0.3))
context.setLineWidth(18)
context.strokeEllipse(in:CGRect(x:150,y:150,width:724,height:724))
context.move(to:CGPoint(x:175,y:510))
for p in [(340,510),(430,700),(535,330),(625,555),(690,510),(850,510)] { context.addLine(to:CGPoint(x:p.0,y:p.1)) }
context.setLineWidth(49); context.setLineCap(.round); context.setLineJoin(.round)
context.setStrokeColor(CGColor(red:0.43,green:1,blue:0.82,alpha:1)); context.strokePath()
let path = CommandLine.arguments.dropFirst().first ?? "ios/BudMon/Assets.xcassets/AppIcon.appiconset/AppIcon.png"
let destination = CGImageDestinationCreateWithURL(URL(fileURLWithPath:path) as CFURL,UTType.png.identifier as CFString,1,nil)!
CGImageDestinationAddImage(destination,context.makeImage()!,nil)
assert(CGImageDestinationFinalize(destination))
