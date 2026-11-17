output "vpc_id" {
  description = "VPC ID."
  value       = aws_vpc.this.id
}

output "vpc_cidr" {
  description = "VPC CIDR block."
  value       = aws_vpc.this.cidr_block
}

output "private_subnet_ids" {
  description = "Private subnets for nodes and pods, one per AZ."
  value       = aws_subnet.private[*].id
}

output "public_subnet_ids" {
  description = "Public subnets for internet facing load balancers."
  value       = aws_subnet.public[*].id
}

output "azs" {
  description = "Availability zones in use."
  value       = local.azs
}
